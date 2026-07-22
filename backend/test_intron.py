import json
import urllib.request
import time

url = 'https://speech.intron.health/speech/transcribe/da8e53f7-941b-4581-b1ef-a76aed98f0a2?mode=next&seq_num=0'

# Fetch projects
try:
    pr = urllib.request.urlopen('http://127.0.0.1:8002/api/v1/projects/', timeout=10)
    projects = json.load(pr)
    proj_id = projects[0]['id']
    print('PROJECT_ID', proj_id)
except Exception as e:
    print('PROJECTS_ERR', e)
    raise

# Create job
try:
    data = {'project': proj_id, 'input_source': 'url', 'source_url': url, 'file_path': ''}
    req = urllib.request.Request('http://127.0.0.1:8002/api/v1/transcriptions/', data=json.dumps(data).encode('utf-8'), headers={'Content-Type': 'application/json'})
    resp = urllib.request.urlopen(req, timeout=10)
    job = json.load(resp)
    print('CREATED', json.dumps({'id': job.get('id'), 'status': job.get('status')}))
except Exception as e:
    print('CREATE_ERR', e)
    raise

# Poll job
for i in range(12):
    try:
        r = urllib.request.urlopen(f"http://127.0.0.1:8002/api/v1/transcriptions/{job['id']}/", timeout=10)
        j = json.load(r)
        print('POLL', i, j.get('status'), j.get('error_message'))
        if j.get('status') in ('completed', 'failed'):
            break
    except Exception as e:
        print('POLLERR', e)
    time.sleep(1)

# Fetch page HTML
try:
    p = urllib.request.urlopen(url, timeout=10)
    c = p.read().decode('utf-8', errors='ignore')
    print('PAGE_FETCH_OK', len(c))
    print(c[:1000])
except Exception as e:
    print('PAGE_FETCH_ERR', e)
