import json, sys
sys.path.insert(0, '.')
from src.core.config import get_config
from src.core.logger import get_cst_now_str

cfg = get_config()
db = cfg.get_storage_path('db_dir')
p_dir = db / 'pipelines'
r_dir = db / 'release'
a_dir = db / 'approval'
pc_dir = db / 'precheck'
for d in [p_dir, r_dir, a_dir, pc_dir]:
    d.mkdir(parents=True, exist_ok=True)

now = get_cst_now_str()
ts_day = now[:10]

# ====== 1. 手动回滚记录 ======
rid1 = 'REL_REAL_A001'
pipeline1 = {
    'release_id': rid1,
    'request': {
        'release_id': rid1, 'version': 'v3.1.0', 'title': 'Hotfix-支付网关超时',
        'description': '', 'release_type': 'hotfix',
        'submitted_by': 'tech_lead@company.com',
        'submitted_at': f'{ts_day} 09:00:00', 'package_url': '', 'target_stations': [],
        'changelog': '', 'hotfix_reason': '支付网关超时率飙升', 'rollback_version': 'v3.0.0',
        'additional_info': {}
    },
    'status': 'rollback_completed', 'current_step': 'rollback_manual',
    'step_history': [
        {'timestamp': f'{ts_day} 09:00:00', 'action': 'SUBMIT', 'detail': '提交'},
        {'timestamp': f'{ts_day} 09:01:00', 'action': 'APPROVAL_COMPLETE', 'detail': '审批通过'},
        {'timestamp': f'{ts_day} 09:05:00', 'action': 'RELEASE_START', 'detail': '灰度启动'},
        {'timestamp': f'{ts_day} 09:18:00', 'action': 'MANUAL_ROLLBACK', 'detail': '手动回滚'},
    ],
    'created_at': f'{ts_day} 09:00:00', 'updated_at': f'{ts_day} 09:19:00', 'error_message': ''
}
with open(p_dir / f'{rid1}.json', 'w', encoding='utf-8') as f:
    json.dump(pipeline1, f, ensure_ascii=False, indent=2)

session1 = {
    'release_id': rid1, 'version': 'v3.1.0', 'rollback_version': 'v3.0.0',
    'stages': [
        {'stage': 1, 'name': '偏远驿站', 'status': 'completed', 'station_types': [], 'region_priority': [],
         'scale_percent': 10, 'observation_minutes': 5,
         'affected_stations': ['A01', 'A02'], 'started_at': '', 'completed_at': ''},
    ],
    'stations': [],
    'created_at': f'{ts_day} 09:05:00', 'status': 'rollback_completed',
    'current_stage_index': 0, 'circuit_breaker_state': 'open',
    'active_monitoring': False, 'monitoring_started_at': '',
    'events': [
        {
            'event_id': 'CBE_MANUAL_001',
            'release_id': rid1, 'version': 'v3.1.0',
            'trigger_stage': 1, 'trigger_metric': 'manual_trigger',
            'trigger_value': 0.0, 'threshold': 0.0,
            'affected_stations': ['A01', 'A02', 'A03'],
            'triggered_at': f'{ts_day} 09:18:00',
            'rollback_started': f'{ts_day} 09:18:01',
            'rollback_completed': f'{ts_day} 09:18:52',
            'rollback_duration_seconds': 51,
            'rollback_successful': True
        }
    ],
    'all_affected_stations': ['A01', 'A02', 'A03'],
    'completed_at': f'{ts_day} 09:18:52', 'final_result': 'rollback'
}
with open(r_dir / f'{rid1}.json', 'w', encoding='utf-8') as f:
    json.dump(session1, f, ensure_ascii=False, indent=2)

# ====== 2. 自动熔断回滚记录 ======
rid2 = 'REL_REAL_E001'
pipeline2 = {
    'release_id': rid2,
    'request': {
        'release_id': rid2, 'version': 'v3.2.0', 'title': '常规发布-固件升级',
        'description': '柜机固件升级v2', 'release_type': 'regular',
        'submitted_by': 'ops_support@company.com',
        'submitted_at': f'{ts_day} 22:00:00', 'package_url': '', 'target_stations': [],
        'changelog': '', 'hotfix_reason': '', 'rollback_version': 'v3.1.0',
        'additional_info': {}
    },
    'status': 'rollback_completed', 'current_step': 'rollback_auto',
    'step_history': [
        {'timestamp': f'{ts_day} 22:00:00', 'action': 'SUBMIT', 'detail': '提交'},
        {'timestamp': f'{ts_day} 22:20:00', 'action': 'APPROVAL_COMPLETE', 'detail': '审批通过'},
        {'timestamp': f'{ts_day} 22:25:00', 'action': 'RELEASE_START', 'detail': '灰度启动'},
        {'timestamp': f'{ts_day} 22:42:00', 'action': 'CIRCUIT_BREAKER_TRIGGER',
         'detail': '柜机离线率 8.2% > 5%'},
    ],
    'created_at': f'{ts_day} 22:00:00', 'updated_at': f'{ts_day} 22:43:30', 'error_message': ''
}
with open(p_dir / f'{rid2}.json', 'w', encoding='utf-8') as f:
    json.dump(pipeline2, f, ensure_ascii=False, indent=2)

session2 = {
    'release_id': rid2, 'version': 'v3.2.0', 'rollback_version': 'v3.1.0',
    'stages': [
        {'stage': 1, 'name': '偏远驿站', 'status': 'completed', 'station_types': [], 'region_priority': [],
         'scale_percent': 10, 'observation_minutes': 10,
         'affected_stations': ['E01'], 'started_at': '', 'completed_at': ''},
    ],
    'stations': [],
    'created_at': f'{ts_day} 22:25:00', 'status': 'rollback_completed',
    'current_stage_index': 0, 'circuit_breaker_state': 'open',
    'active_monitoring': False, 'monitoring_started_at': '',
    'events': [
        {
            'event_id': 'CBE_AUTO_001',
            'release_id': rid2, 'version': 'v3.2.0',
            'trigger_stage': 1, 'trigger_metric': 'terminal_offline_rate',
            'trigger_value': 0.082, 'threshold': 0.05,
            'affected_stations': ['E01', 'E02', 'E03', 'E04', 'E05'],
            'triggered_at': f'{ts_day} 22:42:00',
            'rollback_started': f'{ts_day} 22:42:01',
            'rollback_completed': f'{ts_day} 22:43:30',
            'rollback_duration_seconds': 89,
            'rollback_successful': True
        }
    ],
    'all_affected_stations': ['E01', 'E02', 'E03', 'E04', 'E05'],
    'completed_at': f'{ts_day} 22:43:30', 'final_result': 'rollback'
}
with open(r_dir / f'{rid2}.json', 'w', encoding='utf-8') as f:
    json.dump(session2, f, ensure_ascii=False, indent=2)

# ====== 3. 审批记录 ======
approval1 = {
    'release_id': rid1, 'release_type': 'hotfix', 'channel_name': '紧急热修复',
    'created_at': f'{ts_day} 09:00:30', 'started_at': f'{ts_day} 09:00:30',
    'completed_at': f'{ts_day} 09:01:00', 'status': 'completed', 'overall_passed': True,
    'total_duration_hours': 0.008, 'rejected_by': None, 'rejected_reason': None,
    'nodes': [
        {'role': 'operations', 'name': '运营审批(紧急)', 'status': 'approved',
         'approved_by': 'oncall@company.com', 'approved_at': f'{ts_day} 09:00:45',
         'comment': '紧急放行', 'signature': ''},
        {'role': 'station_manager', 'name': '驿站负责人审批(紧急)', 'status': 'approved',
         'approved_by': 'oncall@company.com', 'approved_at': f'{ts_day} 09:00:50',
         'comment': '已通知站长群', 'signature': ''},
        {'role': 'tech', 'name': '技术审批(紧急)', 'status': 'approved',
         'approved_by': 'tech_lead@company.com', 'approved_at': f'{ts_day} 09:01:00',
         'comment': '代码已确认', 'signature': ''},
    ],
    'channel_config': {'parallel_approval': True}
}
with open(a_dir / f'{rid1}.json', 'w', encoding='utf-8') as f:
    json.dump(approval1, f, ensure_ascii=False, indent=2)

approval2 = {
    'release_id': rid2, 'release_type': 'regular', 'channel_name': '标准审批',
    'created_at': f'{ts_day} 22:01:00', 'started_at': f'{ts_day} 22:01:00',
    'completed_at': f'{ts_day} 22:20:00', 'status': 'completed', 'overall_passed': True,
    'total_duration_hours': 0.32, 'rejected_by': None, 'rejected_reason': None,
    'nodes': [
        {'role': 'operations', 'name': '运营审批', 'status': 'approved',
         'approved_by': 'ops_mgr@company.com', 'approved_at': f'{ts_day} 22:07:00',
         'comment': '', 'signature': ''},
        {'role': 'station_manager', 'name': '驿站负责人审批', 'status': 'approved',
         'approved_by': 'station_head@company.com', 'approved_at': f'{ts_day} 22:13:00',
         'comment': '', 'signature': ''},
        {'role': 'tech', 'name': '技术审批', 'status': 'approved',
         'approved_by': 'tech_lead@company.com', 'approved_at': f'{ts_day} 22:20:00',
         'comment': '', 'signature': ''},
    ],
    'channel_config': {'parallel_approval': False}
}
with open(a_dir / f'{rid2}.json', 'w', encoding='utf-8') as f:
    json.dump(approval2, f, ensure_ascii=False, indent=2)

print('Created:')
print(f'  手动回滚: {rid1} (v3.1.0, hotfix)')
print(f'  自动熔断: {rid2} (v3.2.0, regular)')
print(f'  Expected: 2 rollback records total')
