// Generates src/api/schema.gen.ts from the frozen OpenAPI document (contract/api/openapi-v1.json).
// openapi-typescript drives the TypeScript 5 compiler API, which TypeScript 7 (the type checker of this project)
// no longer ships, so its `typescript` import is resolved to the `typescript5` alias for this script only.
import { readFile, writeFile } from 'node:fs/promises';
import { registerHooks } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

registerHooks({
  resolve(specifier, context, nextResolve) {
    return nextResolve(specifier === 'typescript' ? 'typescript5' : specifier, context);
  },
});

const here = dirname(fileURLToPath(import.meta.url));
const source = resolve(here, '../../contract/api/openapi-v1.json');
const target = resolve(here, '../src/api/schema.gen.ts');

const { default: openapiTS, astToString } = await import('openapi-typescript');
const doc = JSON.parse(await readFile(source, 'utf8'));
const ast = await openapiTS(doc, { silent: true, alphabetize: true });
const header = '// Generated from contract/api/openapi-v1.json by scripts/gen-api.mjs (openapi-typescript). Do not edit.\n\n';
await writeFile(target, header + astToString(ast));
console.log(`gen-api: ${pathToFileURL(target).pathname.split('/web/')[1]}`);
