import { spawn } from 'node:child_process';
import { writeFileSync, realpathSync } from 'node:fs';
import { resolve } from 'node:path';

const cwd = 'C:/Users/kongx/.trae-cn/skills/wind-mcp-skill';
const script = realpathSync(resolve(cwd, 'scripts/cli.mjs'));
const realCwd = realpathSync(cwd);

function runQuery(toolName, query) {
  return new Promise((resolvePromise) => {
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
      resolvePromise({ exit: code, stdout, stderr });
    });
  });
}

async function main() {
  const queries = [
    { tool: "get_financial_news", query: { query: "万丰奥威研报", top_k: 5 } },
    { tool: "get_financial_news", query: { query: "万丰奥威分析师", top_k: 5 } },
    { tool: "get_financial_news", query: { query: "002085.SZ", top_k: 5 } },
  ];

  for (const q of queries) {
    const r = await runQuery(q.tool, q.query);
    if (r.stdout) {
      try {
        const parsed = JSON.parse(r.stdout);
        const text = parsed?.content?.[0]?.text;
        if (text) {
          const inner = JSON.parse(text);
          if (inner?.data?.items?.length > 0) {
            console.log(`=== query="${q.query.query}" items=${inner.data.items.length} ===`);
            for (const item of inner.data.items) {
              console.log(`---`);
              console.log(`标题: ${item.title}`);
              console.log(`日期: ${item.date}`);
              console.log(`内容: ${(item.content || '').substring(0, 800)}`);
            }
          }
        }
      } catch {}
    }
  }
}

main();