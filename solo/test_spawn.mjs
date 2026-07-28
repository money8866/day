import { spawn } from 'node:child_process';
import { writeFileSync, realpathSync } from 'node:fs';
import { resolve } from 'node:path';

const cwd = 'C:/Users/kongx/.trae-cn/skills/wind-mcp-skill';
const script = realpathSync(resolve(cwd, 'scripts/cli.mjs'));
const realCwd = realpathSync(cwd);

function runQuery(query, toolName) {
  return new Promise((resolve) => {
    const child = spawn('node', [
      script, 'call', 'financial_docs', toolName, JSON.stringify(query)
    ], {
      cwd: realCwd,
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
      env: { ...process.env }
    });
    let stdout = '', stderr = '';
    child.stdout.on('data', (data) => { stdout += data.toString(); });
    child.stderr.on('data', (data) => { stderr += data.toString(); });
    child.on('close', (code) => {
      resolve({ exit: code, stdout, stderr, toolName, query });
    });
  });
}

async function main() {
  // Try multiple queries
  const queries = [
    { query: { query: "贵州茅台", top_k: 5 }, tool: "get_financial_news" },
    { query: { query: "贵州茅台研报", top_k: 5 }, tool: "get_financial_news" },
    { query: { query: "贵州茅台分析师", top_k: 5 }, tool: "get_financial_news" },
    { query: { query: "茅台评级", top_k: 5 }, tool: "get_financial_news" },
    { query: { query: "贵州茅台", top_k: 5 }, tool: "get_company_announcements" },
  ];

  for (const q of queries) {
    const r = await runQuery(q.query, q.tool);
    console.log(`\n=== ${q.tool} | query="${q.query.query}" ===`);
    console.log('EXIT:', r.exit);
    if (r.stdout) {
      try {
        const parsed = JSON.parse(r.stdout);
        const text = parsed?.content?.[0]?.text;
        if (text) {
          const inner = JSON.parse(text);
          console.log('DATA:', JSON.stringify(inner, null, 2).substring(0, 3000));
        } else {
          console.log('RAW:', r.stdout.substring(0, 2000));
        }
      } catch {
        console.log('RAW:', r.stdout.substring(0, 2000));
      }
    }
    if (r.stderr) console.log('STDERR:', r.stderr.substring(0, 500));
  }
}

main();