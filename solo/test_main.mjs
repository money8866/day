import { pathToFileURL } from 'node:url';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const cwd = process.cwd();
console.log('cwd:', cwd);

const argv1 = process.argv[1];
console.log('argv[1]:', JSON.stringify(argv1));

const resolved = resolve(argv1);
console.log('resolved:', JSON.stringify(resolved));

const pfu = pathToFileURL(argv1).href;
console.log('pathToFileURL(argv1):', pfu);

const metaUrl = import.meta.url;
console.log('meta.url:', metaUrl);

console.log('IS_MAIN:', argv1 && metaUrl === pfu);