const https = require('https');
const options = {
  hostname: 'en.wikipedia.org',
  path: '/w/api.php?action=parse&page=Mercedes_Sosa&prop=wikitext&format=json',
  headers: {'User-Agent': 'Mozilla/5.0'}
};
const req = https.get(options, res => {
  let data = '';
  res.on('data', chunk => data += chunk);
  res.on('end', () => {
    const parsed = JSON.parse(data);
    const text = parsed.parse.wikitext['*'];
    const lines = text.split('\n');
    lines.forEach((line, i) => {
      if (line.match(/Studio|album/i) || (line.includes('200') && line.match(/200[0-9]/))) {
        console.log(i + ': ' + line.substring(0, 200));
      }
    });
  });
});
req.on('error', e => console.error('Error:', e.message));
req.setTimeout(10000, () => { console.error('Timeout'); req.destroy(); });
