import { request } from '../api/client.js';
import { ORDER } from '../constants/enums.js';
import { esc, percent, timestamp } from '../utils/format.js';

const SECTION_ORDER = ['old_first', 'new_first', 'total'];
const MASK = '—';

const view = item => item.suppressed
  ? `<span class="masked" title="小格保护：该数字已遮罩">${MASK}</span>`
  : esc(item.value);
const rateView = item => item.suppressed
  ? `<span class="masked">${MASK}</span>`
  : percent(item.value);

function matrixTable(section) {
  const data = section.cells;
  return `<table class="brief-matrix"><thead><tr><th scope="col">旧版 ＼ 样张</th>
    <th scope="col">成功</th><th scope="col">未成功</th></tr></thead><tbody>
    <tr><th scope="row">成功</th><td>${view(data.both_ok)}</td><td class="discord">${view(data.old_only)}</td></tr>
    <tr><th scope="row">未成功</th><td class="discord">${view(data.new_only)}</th><td>${view(data.neither)}</td></tr>
    </tbody></table>`;
}

function sectionCard(key, section) {
  const title = key === 'total' ? '两个读序合计' : ORDER[key];
  const badge = section.held
    ? '<span class="chip amber">整组缓发</span>'
    : `<span class="chip">完整 ${view(section.n)} 人 · 登记 ${view(section.registered)} 人 · 缺测 ${view(section.missing)} 人</span>`;
  return `<section class="sheet brief-section"><div class="section-title"><h2>${esc(title)}</h2>${badge}</div>
    ${section.held ? '<p class="warning">该读序相对已发布版本仅有 1–4 人入组、退出或换格，按小格保护规则缓发本组新数。</p>' : matrixTable(section)}
    <dl class="brief-rates"><div><dt>旧版答对率</dt><dd>${rateView(section.old_rate)}<small>${view(section.old_success)} 人答对</small></dd></div>
      <div><dt>易读样张答对率</dt><dd>${rateView(section.new_rate)}<small>${view(section.new_success)} 人答对</small></dd></div></dl></section>`;
}

function heldNote(payload) {
  const lines = [];
  for (const order of payload.held_orders || []) {
    for (const item of payload.held_detail?.[order] || []) {
      lines.push(`复验 #${item.run_id}：${ORDER[order]} 入组 ${item.joined} 人、退出 ${item.left} 人、换格 ${item.moved} 人`);
    }
  }
  return lines.length ? `<section class="sheet brief-note"><h2>缓发说明</h2><ul>${lines.map(line => `<li>${esc(line)}</li>`).join('')}</ul></section>` : '';
}

export async function mountPublicBrief(root) {
  const match = location.hash.match(/^#\/?brief\/([A-Za-z0-9_-]{24,64})/);
  const token = match?.[1];
  try {
    const payload = await request(`/public/briefs/${encodeURIComponent(token)}`, { anonymous: true });
    root.innerHTML = `<div class="public-wrap">
      <header class="public-head"><a class="brand"><span class="seal" aria-hidden="true">平渡</span><span><b>平渡展陈</b><small>易读展签复验 · 公开简报</small></span></a>
        <span class="chip green">会签已发布</span></header>
      <section class="sheet brief-hero"><div><p class="eyebrow">${esc(payload.study_code)} · 样本修订 R${esc(payload.revision)}</p>
        <h1>${esc(payload.study_title)}</h1><p class="lede">${esc(payload.station)} · ${esc(payload.reading_task)}</p></div>
        <dl class="facts"><div><dt>发布人</dt><dd>${esc(payload.signoff.signed_by)}</dd></div>
          <div><dt>会签时间</dt><dd>${esc(timestamp(payload.signoff.signed_at))}</dd></div>
          <div><dt>运行号 / 方法</dt><dd class="mono">#${esc(payload.run_id)} · ${esc(payload.method)}</dd></div></dl>
        <blockquote class="brief-note-text">${esc(payload.signoff.note)}</blockquote>
        <div class="actions"><button class="primary" id="brief-csv">${esc('下载聚合 CSV')}</button>
          <button id="brief-copy">复制本页链接</button></div></section>
      <section class="sheet brief-versions"><h2>对照版本</h2><div class="brief-version-grid">
        <div><small>旧版</small><b class="mono">${esc(payload.versions.old.code)}</b><span>${esc(payload.versions.old.title)}</span><span>${esc(payload.versions.old.type_pt)} pt · ${Number(payload.versions.old.line_height).toFixed(2)} 倍行距</span></div>
        <div><small>易读样张</small><b class="mono">${esc(payload.versions.new.code)}</b><span>${esc(payload.versions.new.title)}</span><span>${esc(payload.versions.new.type_pt)} pt · ${Number(payload.versions.new.line_height).toFixed(2)} 倍行距</span></div>
      </div></section>
      <div class="brief-grid">${SECTION_ORDER.map(key => sectionCard(key, payload.sections[key])).join('')}</div>
      ${heldNote(payload)}
      <section class="sheet brief-note"><h2>数字遮罩口径</h2><p class="muted">${esc(payload.suppression_rule)}</p>
        <p class="muted">本页只呈现按读序聚合的四格人数、组人数与两版答对率，不含任何观众编号或逐条记录。</p>
        <p class="mono brief-checksum" title="固定快照校验值">SHA-256 ${esc(payload.checksum)}</p></section>
      <footer class="public-foot">平渡展陈 · 易读展签复验公开简报 · 链接仅对应 R${esc(payload.revision)} 固定版本</footer>
    </div>`;

    document.querySelector('#brief-csv').addEventListener('click', async event => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        const blob = await request(`/public/briefs/${encodeURIComponent(token)}/brief.csv`,
          { anonymous: true, download: true });
        const url = URL.createObjectURL(blob);
        const anchor = Object.assign(document.createElement('a'),
          { href: url, download: `brief-${payload.study_code}-r${payload.revision}.csv` });
        anchor.click();
        setTimeout(() => URL.revokeObjectURL(url), 3000);
      } finally {
        button.disabled = false;
      }
    });
    document.querySelector('#brief-copy').addEventListener('click', async event => {
      const button = event.currentTarget;
      try {
        await navigator.clipboard.writeText(location.href);
        button.textContent = '链接已复制';
        setTimeout(() => { button.textContent = '复制本页链接'; }, 2000);
      } catch {
        button.textContent = '请直接复制地址栏链接';
      }
    });
  } catch (error) {
    root.innerHTML = `<div class="public-wrap"><header class="public-head"><span class="brand"><span class="seal">平渡</span><span><b>平渡展陈</b><small>易读展签复验 · 公开简报</small></span></span></header>
      <section class="sheet empty"><h3>无法打开简报</h3><p>${esc(error.message)}</p><p class="muted">请向发布人确认链接是否完整。简报按固定版本冻结，不随样本修订更新。</p></section></div>`;
  }
}
