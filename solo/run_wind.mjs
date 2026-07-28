import { spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const scriptPath = resolve('C:/Users/kongx/.trae-cn/skills/wind-mcp-skill/scripts/cli.mjs');

const child = spawn('node', [
  scriptPath,
  'call',
  'financial_docs',
  'get_financial_news',
  '{"query":"贵州茅台","top_k":5}'
], {
  cwd: 'C:/Users/kongx/.trae-cn/skills/wind-mcp-skill',
  stdio: ['pipe', 'pipe', 'pipe'],
  windowsHide: true
});

let stdout = '';
let stderr = '';

child.stdout.on('data', (data) => { stdout += data.toString(); });
child.stderr.on('data', (data) => { stderr += data.toString(); });

child.on('close', (code) => {
  writeFileSync('D:/mystock/solo/wind_result.txt', 
    `EXIT: ${code}\nSTDOUT:\n${stdout}\nSTDERR:\n${stderr}`, 'utf8');
  console.log('EXIT:', code);
  console.log('STDOUT:', stdout);
  console.log('STDERR:', stderr);
});