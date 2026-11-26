// Run with `node --test .github/actions/ghcr-cleanup/` (a pre-commit hook does).
const test = require('node:test');
const assert = require('node:assert');
const cleanup = require('./cleanup-ghcr.js');

// A package version as the GitHub API returns it; `day` orders them by age.
const version = (id, day, tags = [], digest = `sha256:${id}`) => ({
  id,
  name: digest,
  created_at: `2026-01-${String(day).padStart(2, '0')}T00:00:00Z`,
  metadata: { container: { tags } },
});

// Fakes for github, core and fetch. `children` maps a manifest digest to the digests its index lists.
function setup({ versions, children = {}, tokenOk = true, unreadable = [], deleteError = () => null }) {
  const deleted = [];
  const log = { warnings: [], errors: [], failed: null };
  const github = {
    paginate: async () => versions,
    rest: {
      packages: {
        getAllPackageVersionsForPackageOwnedByOrg: {},
        deletePackageVersionForOrg: async ({ package_version_id: id }) => {
          const err = deleteError(id);
          if (err) throw err;
          deleted.push(id);
        },
      },
    },
  };
  const summary = { addHeading: () => summary, addTable: () => summary, write: async () => {} };
  const core = {
    summary,
    info: () => {},
    warning: (m) => log.warnings.push(m),
    error: (m) => log.errors.push(m),
    setFailed: (m) => { log.failed = m; },
  };
  const fetch = async (url) => {
    if (url.startsWith('https://ghcr.io/token')) {
      return { ok: tokenOk, status: tokenOk ? 200 : 401, json: async () => ({ token: 'bearer' }) };
    }
    const digest = url.split('/manifests/')[1];
    if (unreadable.includes(digest)) return { ok: false, status: 404 };
    const manifests = (children[digest] ?? []).map((d) => ({ digest: d }));
    return { ok: true, status: 200, json: async () => ({ manifests }) };
  };
  const run = (opts = {}) => cleanup({
    github, core, fetch, owner: 'my-org', pkg: 'my-app', token: 't',
    keepMain: 2, keepDev: 1, dryRun: false, delayMs: 0, ...opts,
  });
  return { run, deleted, log };
}

test('keeps the newest main and dev versions and tags matching neither pattern', async () => {
  const { run, deleted, log } = setup({
    versions: [
      version(1, 1, ['sha-aaa']),
      version(2, 2, ['sha-bbb-111']),
      version(3, 3, ['latest', 'sha-ccc']),
      version(4, 4, ['dev-ddd']),
      version(5, 5, ['dev-eee-222']),
      version(6, 6, ['buildcache'], 'sha256:cache-index'),
      version(7, 7, ['pr-12']),
      version(8, 8, [], 'sha256:cache-child'),
    ],
    children: { 'sha256:cache-index': ['sha256:cache-child'] },
  });
  await run();
  assert.deepStrictEqual(deleted, [1, 4]); // oldest first
  assert.strictEqual(log.failed, null);
});

test('deletes unreferenced untagged versions, including children of a deleted index', async () => {
  const { run, deleted } = setup({
    versions: [
      version(1, 1, ['sha-0a'], 'sha256:old-index'),
      version(2, 2, [], 'sha256:old-child'),
      version(3, 3, ['sha-0b'], 'sha256:new-index'),
      version(4, 4, [], 'sha256:new-child'),
      version(5, 5, [], 'sha256:orphan'),
    ],
    children: { 'sha256:old-index': ['sha256:old-child'], 'sha256:new-index': ['sha256:new-child'] },
  });
  await run({ keepMain: 1 });
  assert.deepStrictEqual(deleted, [1, 2, 5]);
});

test('-1 keeps every tagged version', async () => {
  const { run, deleted } = setup({
    versions: [version(1, 1, ['sha-a']), version(2, 2, ['sha-b']), version(3, 3, ['dev-c']), version(4, 4, ['dev-d'])],
  });
  await run({ keepMain: -1, keepDev: -1 });
  assert.deepStrictEqual(deleted, []);
});

test('dry run deletes nothing', async () => {
  const { run, deleted } = setup({
    versions: [version(1, 1, ['sha-a']), version(2, 2, ['sha-b']), version(3, 3, ['sha-c'])],
  });
  await run({ dryRun: true, keepMain: 1, maxDeletes: 1 });
  assert.deepStrictEqual(deleted, []);
});

test('stops at maxDeletes, oldest first', async () => {
  const { run, deleted } = setup({
    versions: [version(1, 1, ['sha-a']), version(2, 2, ['sha-b']), version(3, 3, ['sha-c']), version(4, 4, ['sha-d'])],
  });
  await run({ keepMain: 1, maxDeletes: 2 });
  assert.deepStrictEqual(deleted, [1, 2]);
});

test('leaves untagged versions alone when the registry cannot be read', async () => {
  const versions = [version(1, 1, ['sha-a'], 'sha256:index'), version(2, 2, [], 'sha256:orphan')];
  for (const fakes of [{ tokenOk: false }, { unreadable: ['sha256:index'] }]) {
    const { run, deleted, log } = setup({ versions, ...fakes });
    await run();
    assert.deepStrictEqual(deleted, []);
    assert.match(log.warnings.join('\n'), /untagged versions left untouched/);
  }
});

test('a rate limit stops the run without failing it', async () => {
  const limited = Object.assign(new Error('API rate limit exceeded'), { status: 403 });
  const { run, deleted, log } = setup({
    versions: [version(1, 1, ['sha-a']), version(2, 2, ['sha-b']), version(3, 3, ['sha-c']), version(4, 4, ['sha-d'])],
    deleteError: (id) => (id === 2 ? limited : null),
  });
  await run({ keepMain: 1 });
  assert.deepStrictEqual(deleted, [1]);
  assert.strictEqual(log.failed, null);
  assert.match(log.warnings.join('\n'), /rate limited after 1 deletions/);
});

test('another delete error is reported, the rest still go, and the run fails', async () => {
  const { run, deleted, log } = setup({
    versions: [version(1, 1, ['sha-a']), version(2, 2, ['sha-b']), version(3, 3, ['sha-c'])],
    deleteError: (id) => (id === 1 ? Object.assign(new Error('boom'), { status: 500 }) : null),
  });
  await run({ keepMain: 1 });
  assert.deepStrictEqual(deleted, [2]);
  assert.strictEqual(log.failed, '1 deletions failed');
});
