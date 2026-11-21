import requests
import time

url = 'https://en.wikipedia.org/w/api.php?action=parse&page=Mercedes_Sosa&prop=wikitext&format=json'
headers = {'User-Agent': 'Mozilla/5.0'}

for attempt in range(3):
    try:
        r = requests.get(url, headers=headers, timeout=15, proxies={'http': None, 'https': None})
        data = r.json()
        text = data['parse']['wikitext']['*']
        
        lines = text.split('\n')
        for i, line in enumerate(lines):
            if 'Studio' in line or 'album' in line.lower() or ('200' in line and any(str(y) in line for y in range(2000, 2010))):
                print(f"{i}: {line[:200]}")
        break
    except Exception as e:
        print(f"Attempt {attempt+1} failed: {e}")
        time.sleep(2)
