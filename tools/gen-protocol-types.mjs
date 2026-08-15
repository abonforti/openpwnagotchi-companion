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
  }

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
