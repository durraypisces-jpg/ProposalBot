const fs = require('fs');
const { execSync } = require('child_process');
const { chromium } = require('playwright');
const dir = __dirname;
const header = `<div style="width:100%;font-family:Carlito,Calibri,sans-serif;font-size:8.5pt;color:#6B7684;margin:0 20mm;padding-bottom:4pt;border-bottom:0.6pt solid #C9D1DA;text-align:right;">AI Reporting Assistant | Proposal &amp; Requirements</div>`;
const footer = `<div style="width:100%;font-family:Carlito,Calibri,sans-serif;font-size:8.5pt;color:#6B7684;margin:0 20mm;display:flex;justify-content:space-between;"><span>Confidential – Draft v1.1</span><span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>`;
function html(tocPages) {
  let h = fs.readFileSync(`${dir}/proposal.html`, 'utf8');
  h = h.replace('<!--ARCH-->', fs.readFileSync(`${dir}/arch.svg`, 'utf8')).replace('<!--ROAD-->', fs.readFileSync(`${dir}/road.svg`, 'utf8'));
  const items = [...h.matchAll(/data-toc="([^"]+)"/g)].map(m => m[1]);
  const toc = items.map(t => `<li><span class="n">${t}</span><span class="dots"></span><span class="pg">${tocPages[t] ?? '0'}</span></li>`).join('\n');
  return { h: h.replace('<!--TOC-->', toc), items };
}
(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome' }).catch(() => chromium.launch());
  const page = await browser.newPage();
  const render = async (h, out, hf) => {
    await page.setContent(h, { waitUntil: 'load' });
    await page.pdf({ path: out, format: 'A4', printBackground: true, preferCSSPageSize: true,
      displayHeaderFooter: hf, headerTemplate: header, footerTemplate: footer });
  };
  let { h, items } = html({});
  await render(h, `${dir}/pass1.pdf`, true);
  const n = Number(execSync(`pdfinfo ${dir}/pass1.pdf`).toString().match(/Pages:\s+(\d+)/)[1]);
  const pages = {};
  for (let p = 3; p <= n; p++) {
    const txt = execSync(`pdftotext -f ${p} -l ${p} -layout ${dir}/pass1.pdf -`).toString();
    for (const t of items) if (!pages[t] && txt.split('\n').some(l => l.trim() === t)) pages[t] = p;
  }
  console.log(pages);
  ({ h } = html(pages));
  await render(h, `${dir}/body.pdf`, true);
  await render(h, `${dir}/nohf.pdf`, false);
  await browser.close();
})();
