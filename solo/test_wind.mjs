import { execSync } from 'node:child_process';

const cwd = 'C:/Users/kongx/.trae-cn/skills/wind-mcp-skill';
const script = 'C:/Users/kongx/.trae-cn/skills/wind-mcp-skill/scripts/cli.mjs';

try {
  const r = execSync(`node "${script}" call financial_docs get_financial_news "{\\"query\\":\\"贵州茅台\\",\\"top_k\\":5}"`, {
    cwd: cwd,
    encoding: 'utf8',
    maxBuffer: 50 * 1024 * 1024,
    stdio: ['pipe', 'pipe', 'pipe']
  });
  console.log("STDOUT LEN:", r.length);
  console.log("===STDOUT===");
  console.log(r);
  console.log("===END===");
} catch(e) {
  console.log("ERROR:", e.message);
  console.log("STDOUT:", e.stdout);
  console.log("STDERR:", e.stderr);
  console.log("STATUS:", e.status);
}