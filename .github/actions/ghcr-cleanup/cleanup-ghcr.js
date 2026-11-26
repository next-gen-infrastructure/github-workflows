// Keeps the newest main (latest / sha-*) and dev (dev-*) image versions in GHCR and deletes the rest.
// Untagged versions are the platform/attestation manifests of tagged images; only unreferenced ones go.
const MAIN_TAG = /^(latest|sha-[0-9a-f]+(-[0-9a-f]+)?)$/; // optional -<submodule sha> suffix
const DEV_TAG = /^dev-[0-9a-f]+(-[0-9a-f]+)?$/;
const MANIFEST_TYPES = [
  'application/vnd.oci.image.index.v1+json',
  'application/vnd.docker.distribution.manifest.list.v2+json',
  'application/vnd.oci.image.manifest.v1+json',
  'application/vnd.docker.distribution.manifest.v2+json',
].join(', ');

// keep < 0 means keep all
const top = (list, n) => (n < 0 ? list : list.slice(0, n));
const tagsOf = (v) => v.metadata?.container?.tags ?? [];
const newestFirst = (a, b) => new Date(b.created_at) - new Date(a.created_at);
const oldestFirst = (a, b) => -newestFirst(a, b);
const describe = (v) => `${v.id} ${v.created_at.slice(0, 10)} ${tagsOf(v).join(',') || v.name.slice(0, 19)}`;
const isRateLimit = (err) => err.status === 429 || (err.status === 403 && /rate limit/i.test(err.message));
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function referencedDigests({ kept, owner, pkg, token, core, fetch }) {
  const repo = `${owner}/${pkg}`.toLowerCase();
  const auth = await fetch(`https://ghcr.io/token?service=ghcr.io&scope=repository:${repo}:pull`, {
    headers: { Authorization: `Basic ${Buffer.from(`x:${token}`).toString('base64')}` },
  });
  if (!auth.ok) {
    core.warning(`ghcr token exchange failed (${auth.status}); untagged versions left untouched`);
    return null;
  }
  const bearer = (await auth.json()).token;
  const digests = new Set();
  for (const v of kept) {
    const res = await fetch(`https://ghcr.io/v2/${repo}/manifests/${v.name}`, {
      headers: { Authorization: `Bearer ${bearer}`, Accept: MANIFEST_TYPES },
    });
    if (!res.ok) {
      core.warning(`manifest ${v.name} not readable (${res.status}); untagged versions left untouched`);
      return null;
    }
    for (const child of (await res.json()).manifests ?? []) digests.add(child.digest);
  }
  return digests;
}

// GitHub allows 80 content changes per minute and 500 per hour; 400 per run at 800 ms spacing stays under both.
module.exports = async ({
  github, core, owner, pkg, keepMain, keepDev, dryRun, token,
  maxDeletes = 400, delayMs = 800, fetch = globalThis.fetch,
}) => {
  const versions = await github.paginate(github.rest.packages.getAllPackageVersionsForPackageOwnedByOrg, {
    package_type: 'container', package_name: pkg, org: owner, per_page: 100, state: 'active',
  });

  const main = versions.filter((v) => tagsOf(v).some((t) => MAIN_TAG.test(t))).sort(newestFirst);
  const dev = versions.filter((v) => tagsOf(v).some((t) => DEV_TAG.test(t))).sort(newestFirst);
  const untagged = versions.filter((v) => tagsOf(v).length === 0);
  const other = versions.filter((v) => !main.includes(v) && !dev.includes(v) && !untagged.includes(v));

  const keep = new Set([...top(main, keepMain), ...top(dev, keepDev), ...other]);
  const stale = [...new Set([...main, ...dev].filter((v) => !keep.has(v)))].sort(oldestFirst);

  const referenced = await referencedDigests({ kept: [...keep], owner, pkg, token, core, fetch });
  const orphans = (referenced ? untagged.filter((v) => !referenced.has(v.name)) : []).sort(oldestFirst);
  const toDelete = [...stale, ...orphans];
  const batch = dryRun ? toDelete : toDelete.slice(0, maxDeletes);
  const runsNeeded = Math.ceil(toDelete.length / maxDeletes);

  core.info(`${versions.length} versions: keeping ${keep.size} tagged + ${untagged.length - orphans.length} untagged, ${toDelete.length} to delete${dryRun ? ' (dry run)' : `, ${batch.length} this run`}`);
  let deleted = 0;
  let failed = 0;
  for (const v of batch) {
    if (dryRun) {
      core.info(`would delete ${describe(v)}`);
      continue;
    }
    try {
      if (deleted > 0) await sleep(delayMs);
      await github.rest.packages.deletePackageVersionForOrg({
        package_type: 'container', package_name: pkg, org: owner, package_version_id: v.id,
      });
      deleted++;
      core.info(`deleted ${describe(v)}`);
    } catch (err) {
      if (isRateLimit(err)) {
        core.warning(`rate limited after ${deleted} deletions; ${toDelete.length - deleted} left for the next run`);
        break;
      }
      failed++;
      core.error(`delete ${describe(v)}: ${err.message}`);
    }
  }

  const heading = dryRun
    ? `Would delete ${toDelete.length} of ${versions.length} image versions (${runsNeeded} run${runsNeeded === 1 ? '' : 's'} at ${maxDeletes} per run)`
    : `Deleted ${deleted} of ${toDelete.length} stale image versions, ${toDelete.length - deleted} left for the next run`;
  const row = (action) => (v) => [action, v.created_at.slice(0, 10), tagsOf(v).join(', ') || '(untagged)'];
  await core.summary
    .addHeading(heading)
    .addTable([
      [{ data: 'Action', header: true }, { data: 'Created', header: true }, { data: 'Tags', header: true }],
      ...[...keep].sort(newestFirst).map(row('keep')),
      ...batch.map(row(dryRun ? 'would delete' : 'delete')),
    ])
    .write();
  if (failed) core.setFailed(`${failed} deletions failed`);
};
