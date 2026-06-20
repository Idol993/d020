import json, sys
sys.path.insert(0, '.')
from src.core.config import get_config
from src.core.logger import get_cst_now_str

cfg = get_config()
db = cfg.get_storage_path('db_dir')
now = get_cst_now_str()
ts_day = now[:10]
rid = 'REL_20260621061302_983'

p_dir = db / 'pipelines'
r_dir = db / 'release'
p_dir.mkdir(parents=True, exist_ok=True)
r_dir.mkdir(parents=True, exist_ok=True)

with open(p_dir / f'{rid}.json', 'r', encoding='utf-8') as f:
    p = json.load(f)
p['status'] = 'grayscale_in_progress'
p['current_step'] = 'grayscale_stage2'
p['step_history'].append({'timestamp': now, 'action': 'RELEASE_START', 'detail': '灰度启动'})
p['updated_at'] = now
with open(p_dir / f'{rid}.json', 'w', encoding='utf-8') as f:
    json.dump(p, f, ensure_ascii=False, indent=2)

session = {
    'release_id': rid, 'version': 'v3.0.0', 'rollback_version': 'v2.9.0',
    'stages': [
        {'stage': 1, 'name': '偏远驿站', 'status': 'completed', 'station_types': [], 'region_priority': [],
         'scale_percent': 10, 'observation_minutes': 15,
         'affected_stations': ['S401', 'S402'], 'started_at': '', 'completed_at': ''},
        {'stage': 2, 'name': '社区驿站', 'status': 'in_progress', 'station_types': [], 'region_priority': [],
         'scale_percent': 30, 'observation_minutes': 20,
         'affected_stations': ['S403', 'S404', 'S405'], 'started_at': '', 'completed_at': ''},
    ],
    'stations': [],
    'created_at': now, 'status': 'in_progress',
    'current_stage_index': 1, 'circuit_breaker_state': 'closed',
    'active_monitoring': True, 'monitoring_started_at': now,
    'events': [],
    'all_affected_stations': ['S401', 'S402', 'S403', 'S404', 'S405'],
    'completed_at': '', 'final_result': ''
}
with open(r_dir / f'{rid}.json', 'w', encoding='utf-8') as f:
    json.dump(session, f, ensure_ascii=False, indent=2)

print(f'Created grayscale session for {rid}')
