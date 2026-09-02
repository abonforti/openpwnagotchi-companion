#!/usr/bin/env node
/**
 * Generates frontend/src/lib/protocol.ts from docs/schemas.
 *
 * docs/schemas is the single source of truth for the wire format (SPEC.md D15).
 * The generated file is committed, and CI re-runs this script and fails if the
 * working tree changes, so the schemas and the TypeScript types cannot drift.
 *
 * Usage: node tools/gen-protocol-types.mjs [--check]
 *   --check  exit 1 if the generated output differs from the file on disk
 *
 * The generator deliberately supports only the JSON Schema subset used by
 * docs/schemas: const, enum, $ref, oneOf, type (including nullable unions),
 * object with properties/required, and array with items. Anything else is a
 * hard error rather than a silent `any` - if you need a new construct, extend
 * this script in the same commit that uses it.
 *
 * SPEC.md 4.3.11 (issue #109): the same walk also emits runtime type guards
 * for the outgoing messages (what this client receives), one per message
 * type plus whatever common.json $defs those messages reach through $ref.
 * The guard subset is exactly the type-level subset above - no more - and
 * hits the same `fail()` for anything it cannot express.
 */

import { readFileSync, writeFileSync, readdirSync } from 'node:fs';
import { join, dirname, basename } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, '..');
const SCHEMA_DIR = join(ROOT, 'docs', 'schemas');
const OUT = join(ROOT, 'frontend', 'src', 'lib', 'protocol.ts');

const fail = (msg) => {
  console.error(`gen-protocol-types: ${msg}`);
  process.exit(1);
};

const readJson = (p) => JSON.parse(readFileSync(p, 'utf8'));

/** `set_mode` -> `SetMode` */
const pascal = (s) => s.split(/[_\-]/).filter(Boolean).map((w) => w[0].toUpperCase() + w.slice(1)).join('');

const PRIMITIVES = { string: 'string', number: 'number', integer: 'number', boolean: 'boolean', null: 'null' };

/** Renders a schema node as a TypeScript type expression. `depth` drives indentation only. */
function tsType(node, path, depth = 0) {
  if (node === true || node === false) fail(`boolean schema at ${path} is not supported`);

  if (node.$ref) {
    // Both forms are used: "common.json#/$defs/X" from a message schema, and the
    // internal "#/$defs/X" from within common.json itself.
    const m = /^(?:common\.json)?#\/\$defs\/(\w+)$/.exec(node.$ref);
    if (!m) fail(`unsupported $ref "${node.$ref}" at ${path}`);
    return m[1];
  }

  if (node.oneOf) return node.oneOf.map((n, i) => tsType(n, `${path}.oneOf[${i}]`, depth)).join(' | ');

  if (node.const !== undefined) return JSON.stringify(node.const);

  if (node.enum) return node.enum.map((v) => JSON.stringify(v)).join(' | ');

  const types = Array.isArray(node.type) ? node.type : node.type === undefined ? [] : [node.type];
  if (types.length === 0) fail(`node at ${path} has no type, $ref, enum, const or oneOf`);

  const parts = types.map((t) => {
    if (t === 'object') {
      const props = node.properties;
      if (!props || Object.keys(props).length === 0) return 'Record<string, never>';
      const required = new Set(node.required ?? []);
      const pad = '  '.repeat(depth + 1);
      const fields = Object.entries(props).map(([key, sub]) => {
        const optional = required.has(key) ? '' : '?';
        const doc = sub.description ? `${pad}/** ${sub.description} */\n` : '';
        return `${doc}${pad}${JSON.stringify(key)}${optional}: ${tsType(sub, `${path}.${key}`, depth + 1)};`;
      });
      return `{\n${fields.join('\n')}\n${'  '.repeat(depth)}}`;
    }
    if (t === 'array') {
      if (!node.items) fail(`array at ${path} has no items`);
      const inner = tsType(node.items, `${path}[]`, depth);
      return /[ |]/.test(inner) ? `Array<${inner}>` : `${inner}[]`;
    }
    const prim = PRIMITIVES[t];
    if (!prim) fail(`unsupported type "${t}" at ${path}`);
    return prim;
  });

  return [...new Set(parts)].join(' | ');
}

/** Renders one named top-level declaration. */
function declaration(name, node, path) {
  const doc = node.description ? `/**\n * ${node.description.replace(/\n/g, '\n * ')}\n */\n` : '';
  const body = tsType(node, path);
  // A bare object literal becomes an interface; everything else a type alias.
  if (body.startsWith('{')) return `${doc}export interface ${name} ${body}\n`;
  return `${doc}export type ${name} = ${body};\n`;
}

// SPEC.md 4.3.11: the primitive check a guard performs for each JSON Schema
// `type`. `integer` is checked the same as `number` - a `typeof` test only,
// no `Number.isInteger` - mirroring tsType's own PRIMITIVES table above,
// which already collapses the two to the same TypeScript `number`.
const PRIMITIVE_CHECK = {
  string: (e) => `typeof ${e} === 'string'`,
  number: (e) => `typeof ${e} === 'number'`,
  integer: (e) => `typeof ${e} === 'number'`,
  boolean: (e) => `typeof ${e} === 'boolean'`,
  null: (e) => `${e} === null`,
};

// SPEC.md 4.3.11: "the discipline is an allowlist of keywords, not a list of
// things that break it". A walk that fails only on what it recognises as
// wrong passes everything it does not recognise at all, which is the same
// silent `true` in a different place - a `minimum` added to an outgoing
// schema must stop the build, not emit a guard with no bound and no error.
// GUARD_ENFORCED_KEYS is what the walk below gives meaning to;
// GUARD_IGNORED_KEYS is what it deliberately skips, each for the reason
// SPEC.md 4.3.11 gives: annotations that describe rather than constrain
// (`$schema`, `$id`, `title`, `description`, `default`), the
// forward-compatibility choice on `additionalProperties`, and
// `contentEncoding`, which appears once, on `screen_image.data.png`, where
// enforcing it would mean base64-decoding a full frame on every mirror
// update to learn what the decoder is about to tell us anyway. Any other
// keyword on any node this walk reaches is a hard `fail()`.
const GUARD_ENFORCED_KEYS = new Set([
  'type', 'const', 'enum', '$ref', 'oneOf', 'properties', 'required', 'items',
]);
const GUARD_IGNORED_KEYS = new Set([
  '$schema', '$id', 'title', 'description', 'default', 'additionalProperties', 'contentEncoding',
]);

function checkGuardKeywords(node, path) {
  for (const key of Object.keys(node)) {
    if (GUARD_ENFORCED_KEYS.has(key) || GUARD_IGNORED_KEYS.has(key)) continue;
    fail(`unsupported keyword "${key}" at ${path} - the guard walk allowlists keywords (SPEC.md 4.3.11)`);
  }
}

/**
 * Renders a schema node as a runtime boolean expression testing whether
 * `expr` (a TypeScript expression string, always of static type `unknown`)
 * satisfies it. Mirrors tsType's subset and its `fail()` discipline exactly:
 * const, enum, $ref, oneOf, type (including nullable unions), object with
 * properties/required, array with items. `depth` only names array element
 * variables uniquely when items are themselves arrays.
 *
 * A `$ref` becomes a call to the named guard (`isX`), never an inline
 * expansion, so the guard for a shared shape (SPEC.md 4.3.11: "common.json
 * $defs reached by $ref") is written once and reused everywhere it appears.
 *
 * Nested object/array checks cast the value under test with `as` rather
 * than relying on TypeScript to narrow a computed property-access
 * expression across `&&`: the cast is only ever reached after the
 * `isRecord`/`Array.isArray` check that guarantees it at runtime, and it
 * keeps every nesting depth this walk produces equally safe to typecheck.
 *
 * Every node this walk visits is checked against the keyword allowlist
 * above before anything else, so a keyword this walk does not know how to
 * enforce stops the build instead of being silently passed (SPEC.md 4.3.11).
 */
function guardExpr(node, expr, path, depth = 0) {
  if (node === true || node === false) fail(`boolean schema at ${path} is not supported`);
  checkGuardKeywords(node, path);

  if (node.$ref) {
    const m = /^(?:common\.json)?#\/\$defs\/(\w+)$/.exec(node.$ref);
    if (!m) fail(`unsupported $ref "${node.$ref}" at ${path}`);
    return `is${m[1]}(${expr})`;
  }

  if (node.oneOf) {
    return `(${node.oneOf.map((n, i) => guardExpr(n, expr, `${path}.oneOf[${i}]`, depth)).join(' || ')})`;
  }

  if (node.const !== undefined) return `${expr} === ${JSON.stringify(node.const)}`;

  if (node.enum) return `(${node.enum.map((v) => `${expr} === ${JSON.stringify(v)}`).join(' || ')})`;

  const types = Array.isArray(node.type) ? node.type : node.type === undefined ? [] : [node.type];
  if (types.length === 0) fail(`node at ${path} has no type, $ref, enum, const or oneOf`);

  const parts = types.map((t) => {
    if (t === 'object') {
      const props = node.properties;
      if (!props || Object.keys(props).length === 0) return `isRecord(${expr})`;
      const required = new Set(node.required ?? []);
      const rec = `(${expr} as Record<string, unknown>)`;
      const fields = Object.entries(props).map(([key, sub]) => {
        const keyExpr = `${rec}[${JSON.stringify(key)}]`;
        const check = guardExpr(sub, keyExpr, `${path}.${key}`, depth);
        return required.has(key) ? check : `(${keyExpr} === undefined || ${check})`;
      });
      return `(isRecord(${expr}) && ${fields.join(' && ')})`;
    }
    if (t === 'array') {
      if (!node.items) fail(`array at ${path} has no items`);
      const item = `item${depth}`;
      const inner = guardExpr(node.items, item, `${path}[]`, depth + 1);
      return `(Array.isArray(${expr}) && (${expr} as unknown[]).every((${item}: unknown) => ${inner}))`;
    }
    const check = PRIMITIVE_CHECK[t];
    if (!check) fail(`unsupported type "${t}" at ${path}`);
    return check(expr);
  });

  return parts.length === 1 ? parts[0] : `(${parts.join(' || ')})`;
}

// Discovers, without emitting anything, every common.json $def a schema
// reaches through $ref - transitively, through $defs that themselves
// reference further $defs. Order is first-discovered, so the emitted guards
// read top-down the same way a reader encounters them. Duplicate-free: a
// $def visited once (from any message) is never walked or emitted twice.
function collectRefs(common, node, seen, order, path) {
  if (node.$ref) {
    const m = /^(?:common\.json)?#\/\$defs\/(\w+)$/.exec(node.$ref);
    if (!m || seen.has(m[1])) return;
    seen.add(m[1]);
    order.push(m[1]);
    collectRefs(common, common.$defs[m[1]], seen, order, `common.$defs.${m[1]}`);
    return;
  }
  if (node.oneOf) {
    for (const [i, n] of node.oneOf.entries()) collectRefs(common, n, seen, order, `${path}.oneOf[${i}]`);
    return;
  }
  if (node.const !== undefined || node.enum) return;
  const types = Array.isArray(node.type) ? node.type : node.type === undefined ? [] : [node.type];
  for (const t of types) {
    if (t === 'object' && node.properties) {
      for (const [key, sub] of Object.entries(node.properties)) {
        collectRefs(common, sub, seen, order, `${path}.${key}`);
      }
    }
    if (t === 'array' && node.items) collectRefs(common, node.items, seen, order, `${path}[]`);
  }
}

function guardDeclaration(fnName, tsName, node, path) {
  return `export function ${fnName}(v: unknown): v is ${tsName} {\n  return ${guardExpr(node, 'v', path)};\n}\n`;
}

function messageFiles(kind) {
  const dir = join(SCHEMA_DIR, kind);
  return readdirSync(dir)
    .filter((f) => f.endsWith('.json'))
    .sort()
    .map((f) => ({ name: basename(f, '.json'), schema: readJson(join(dir, f)) }));
}

function build() {
  const common = readJson(join(SCHEMA_DIR, 'common.json'));
  const chunks = [];

  chunks.push(
    '// GENERATED FILE - DO NOT EDIT.',
    '//',
    '// Source of truth: docs/schemas/*.json (SPEC.md D15).',
    '// Regenerate with: node tools/gen-protocol-types.mjs',
    '// CI fails if this file is out of sync with the schemas.',
    '',
  );

  chunks.push('// ---------------------------------------------------------------------------');
  chunks.push('// Shared shapes (docs/schemas/common.json)');
  chunks.push('// ---------------------------------------------------------------------------', '');
  for (const [name, node] of Object.entries(common.$defs)) {
    chunks.push(declaration(name, node, `common.$defs.${name}`));
  }

  let outgoingFiles = [];
  let outgoingNames = [];

  for (const kind of ['incoming', 'outgoing']) {
    const files = messageFiles(kind);
    const prefix = pascal(kind);
    chunks.push('// ---------------------------------------------------------------------------');
    chunks.push(`// ${prefix} messages (docs/schemas/${kind}/)`);
    chunks.push('// ---------------------------------------------------------------------------', '');

    const names = [];
    for (const { name, schema } of files) {
      if (schema.properties?.type?.const !== name) {
        fail(`${kind}/${name}.json declares type const "${schema.properties?.type?.const}"`);
      }
      const iface = `${prefix}${pascal(name)}`;
      names.push({ wire: name, iface });
      chunks.push(declaration(iface, schema, `${kind}/${name}`));
    }

    chunks.push(`export type ${prefix}Message =\n${names.map((n) => `  | ${n.iface}`).join('\n')};\n`);
    chunks.push(
      `export const ${kind.toUpperCase()}_TYPES = [\n` +
        names.map((n) => `  ${JSON.stringify(n.wire)},`).join('\n') +
        `\n] as const;\n`,
    );
    chunks.push(`export type ${prefix}MessageType = (typeof ${kind.toUpperCase()}_TYPES)[number];\n`);

    if (kind === 'outgoing') {
      outgoingFiles = files;
      outgoingNames = names;
    }
  }

  // SPEC.md 4.3.11 (issue #109): runtime guards for the outgoing side only -
  // what this client receives and has to check "before it is believed".
  // The incoming side is what this client sends; it never validates its own
  // output.
  chunks.push('// ---------------------------------------------------------------------------');
  chunks.push('// Runtime guards for outgoing messages (SPEC.md 4.3.11, issue #109)');
  chunks.push('// ---------------------------------------------------------------------------', '');
  chunks.push(
    'function isRecord(value: unknown): value is Record<string, unknown> {\n' +
      "  return typeof value === 'object' && value !== null && !Array.isArray(value);\n" +
      '}\n',
  );

  const seenRefs = new Set();
  const refOrder = [];
  for (const { name, schema } of outgoingFiles) {
    collectRefs(common, schema, seenRefs, refOrder, `outgoing/${name}`);
  }
  for (const defName of refOrder) {
    chunks.push(guardDeclaration(`is${defName}`, defName, common.$defs[defName], `common.$defs.${defName}`));
  }

  for (const { wire, iface } of outgoingNames) {
    const { schema } = outgoingFiles.find((f) => f.name === wire);
    chunks.push(guardDeclaration(`is${iface}`, iface, schema, `outgoing/${wire}`));
  }

  chunks.push(
    'export function isOutgoingMessage(type: string, v: unknown): v is OutgoingMessage {\n' +
      '  switch (type) {\n' +
      outgoingNames
        .map(({ wire, iface }) => `    case ${JSON.stringify(wire)}: return is${iface}(v);`)
        .join('\n') +
      '\n    default:\n      return false;\n  }\n}\n',
  );

  return chunks.join('\n');
}

const generated = build();

if (process.argv.includes('--check')) {
  let current = '';
  try {
    current = readFileSync(OUT, 'utf8');
  } catch {
    fail(`${OUT} does not exist; run without --check to generate it`);
  }
  if (current !== generated) fail(`${OUT} is out of sync with docs/schemas; regenerate it`);
  console.log('protocol.ts is in sync with docs/schemas');
} else {
  writeFileSync(OUT, generated);
  console.log(`wrote ${OUT}`);
}
