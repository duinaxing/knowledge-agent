"""Print status codes only; never print configuration secrets or provider bodies."""
import json
import httpx
from .config import settings


def main():
    try:
        r = httpx.get(settings.model_base_url.rstrip('/') + '/models',
                      headers={'Authorization': 'Bearer ' + settings.model_api_key.get_secret_value()}, timeout=15)
        result = {'deepseek_http_status': r.status_code}
        if r.status_code == 200:
            result['available_models'] = [m['id'] for m in r.json().get('data', [])]
        print(json.dumps(result))
    except httpx.HTTPError as exc:
        print(json.dumps({'error': type(exc).__name__}))


if __name__ == '__main__':
    main()
