const puppeteer = require('puppeteer');

async function findLink(url) {
  const browser = await puppeteer.launch({ args: ['--no-sandbox', '--disable-setuid-sandbox'] });
  try {
    const page = await browser.newPage();
    await page.goto(url, { waitUntil: 'networkidle2', timeout: 30000 });

    // Try to find anchors or buttons containing "transcribe" or "start"
    const link = await page.evaluate(() => {
      const keywords = [/transcrib/i, /start transcrib/i, /start trans/i, /audio task/i, /task/i];
      // search anchors first
      const anchors = Array.from(document.querySelectorAll('a'));
      for (const a of anchors) {
        try {
          const txt = (a.innerText || a.textContent || '').trim();
          for (const k of keywords) {
            if (txt && k.test(txt)) {
              if (a.href) return a.href;
            }
          }
        } catch (e) {}
      }

      // search buttons
      const buttons = Array.from(document.querySelectorAll('button'));
      for (const b of buttons) {
        try {
          const txt = (b.innerText || b.textContent || '').trim();
          for (const k of keywords) {
            if (txt && k.test(txt)) {
              // if button is inside anchor
              let el = b;
              while (el && el.tagName && el.tagName.toLowerCase() !== 'a') el = el.parentElement;
              if (el && el.href) return el.href;
              // check onclick
              if (b.getAttribute && b.getAttribute('onclick')) return b.getAttribute('onclick');
            }
          }
        } catch (e) {}
      }

      // fallback: look for elements with data-href or href-like attributes
      const els = Array.from(document.querySelectorAll('[data-href],[data-url],[href]'));
      for (const e of els) {
        try {
          const txt = (e.innerText || e.textContent || '').trim();
          for (const k of keywords) {
            if (txt && k.test(txt)) {
              if (e.href) return e.href;
              if (e.getAttribute('data-href')) return e.getAttribute('data-href');
              if (e.getAttribute('data-url')) return e.getAttribute('data-url');
            }
          }
        } catch (er) {}
      }

      return null;
    });

    if (link) {
      console.log(link);
    } else {
      // As a last resort, dump buttons and anchors to stderr for debugging
      const dump = await page.evaluate(() => {
        const items = [];
        const anchors = Array.from(document.querySelectorAll('a')).map(a => ({tag:'a', text: (a.innerText||a.textContent||'').trim(), href: a.href}));
        const buttons = Array.from(document.querySelectorAll('button')).map(b => ({tag:'button', text: (b.innerText||b.textContent||'').trim(), onclick: b.getAttribute('onclick'), parentHref: (function(){let el=b;while(el && el.tagName && el.tagName.toLowerCase()!=='a') el=el.parentElement; return el?el.href:null})()}));
        return {anchors, buttons};
      });
      console.error('NO_LINK_FOUND');
      console.error(JSON.stringify(dump, null, 2));
    }

    await browser.close();
  } catch (e) {
    console.error('ERROR', e && e.message);
    try { await browser.close(); } catch (e) {}
    process.exit(2);
  }
}

if (process.argv.length < 3) {
  console.error('Usage: node find_intron_link.js <url>');
  process.exit(1);
}

const url = process.argv[2];
findLink(url).then(() => process.exit(0));
