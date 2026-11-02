const { chromium } = require('playwright');
const fs = require('fs'), path = require('path');
(async () => {
  const [inFile, outPng, idx] = process.argv.slice(2);
  const xml = fs.readFileSync(inFile, 'utf8');
  const mxDir = path.join(process.env.MXGRAPH_DIR || path.join(__dirname, 'node_modules/mxgraph'), 'javascript');
  const html = `<!doctype html><html><head><meta charset="utf-8">
  <style>body{margin:0;background:#fff;font-family:Helvetica,Arial,sans-serif}#c{position:absolute;left:0;top:0}</style>
  <script>var mxBasePath='file://${mxDir}/src';var mxLoadResources=false;var mxLoadStylesheets=false;</script>
  <script src="file://${mxDir}/mxClient.js"></script></head><body><div id="c"></div></body></html>`;
  const htmlPath = path.join(__dirname, 'page.html');
  fs.writeFileSync(htmlPath, html);
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1200, height: 1100 }, deviceScaleFactor: 2 });
  await page.goto('file://' + htmlPath);
  const res = await page.evaluate(([xml, IDX]) => {
    const doc = mxUtils.parseXml(xml);
    const model = doc.getElementsByTagName('mxGraphModel')[IDX];
    const c = document.getElementById('c');
    const g = new mxGraph(c);
    g.setHtmlLabels(true);
    g.foldingEnabled = false;
    const one = mxUtils.parseXml(mxUtils.getXml(model)).documentElement; new mxCodec(one.ownerDocument).decode(one, g.getModel());
    const b = g.getGraphBounds();
    g.view.setTranslate(-b.x + 20, -b.y + 20);
    const w = Math.ceil(b.width + 40), h = Math.ceil(b.height + 40);
    c.style.width = w + 'px'; c.style.height = h + 'px';
    const svg = c.querySelector('svg');
    svg.setAttribute('width', w); svg.setAttribute('height', h);
    svg.style.background = '#ffffff';
    return { w, h, svg: svg.outerHTML };
  }, [xml, Number(idx || 0)]);
  await page.setViewportSize({ width: res.w, height: res.h });
  await page.screenshot({ path: outPng, clip: { x: 0, y: 0, width: res.w, height: res.h } });
  
  console.log('rendered', res.w, res.h);
  await browser.close();
})();
