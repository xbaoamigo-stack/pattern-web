"""
投資行事曆 API
- 經濟數據日期（CPI、FOMC 等）
- 個股事件（財報、法說會）
- 用戶自定事件
"""

import json
import os
import time

CALENDAR_FILE = os.path.join(os.path.dirname(__file__), 'data', 'calendar_events.json')

# 預設經濟指標日期（2026 年）
DEFAULT_EVENTS = {
    "economic": [
        {"id": 1, "date": "2026-05-22", "time": "12:30", "name": "美國 CPI (年率)", "importance": "high", "type": "economic"},
        {"id": 2, "date": "2026-05-30", "time": "20:30", "name": "美國非農就業人數 (NFP)", "importance": "critical", "type": "economic"},
        {"id": 3, "date": "2026-06-18", "time": "20:00", "name": "FOMC 利率決議", "importance": "critical", "type": "economic"},
        {"id": 4, "date": "2026-07-30", "time": "12:30", "name": "美國 CPI (年率)", "importance": "high", "type": "economic"},
    ],
    "company": [
        {"id": 101, "date": "2026-05-31", "time": "14:00", "name": "台積電 (2330) 2026 Q1 法說會", "importance": "high", "symbol": "2330", "type": "company"},
        {"id": 102, "date": "2026-08-15", "time": "15:00", "name": "台積電 (2330) 2026 Q2 財報", "importance": "medium", "symbol": "2330", "type": "company"},
    ],
    "custom": []
}

def ensure_calendar_file():
    """確保日曆文件存在"""
    os.makedirs(os.path.dirname(CALENDAR_FILE), exist_ok=True)
    if not os.path.exists(CALENDAR_FILE):
        with open(CALENDAR_FILE, 'w', encoding='utf-8') as f:
            json.dump(DEFAULT_EVENTS, f, ensure_ascii=False, indent=2)
        return DEFAULT_EVENTS
    return None

def load_events():
    """載入所有事件"""
    ensure_calendar_file()
    try:
        with open(CALENDAR_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return DEFAULT_EVENTS

def save_events(events):
    """保存事件"""
    ensure_calendar_file()
    with open(CALENDAR_FILE, 'w', encoding='utf-8') as f:
        json.dump(events, f, ensure_ascii=False, indent=2)

def add_event(event_data):
    """新增事件"""
    events = load_events()
    
    # 生成 ID
    all_ids = []
    for cat in events.values():
        if isinstance(cat, list):
            all_ids.extend([e.get('id', 0) for e in cat if isinstance(e, dict)])
    new_id = max(all_ids) + 1 if all_ids else 1
    
    event_data['id'] = new_id
    event_type = event_data.get('type', 'custom')
    
    if event_type not in events:
        events[event_type] = []
    events[event_type].append(event_data)
    
    save_events(events)
    return new_id

def delete_event(event_id):
    """刪除事件"""
    events = load_events()
    for category in events:
        events[category] = [e for e in events[category] if e.get('id') != event_id]
    save_events(events)

def get_events_by_date(start_date=None, end_date=None):
    """按日期範圍取事件"""
    events = load_events()
    result = []
    
    for category in events:
        for event in events[category]:
            event_date = event.get('date')
            if start_date and event_date < start_date:
                continue
            if end_date and event_date > end_date:
                continue
            result.append(event)
    
    # 按日期排序
    result.sort(key=lambda x: x.get('date', ''))
    return result

def get_events_by_symbol(symbol):
    """按股票代碼取事件"""
    events = load_events()
    result = []
    
    for category in ['company', 'custom']:
        if category in events:
            for event in events[category]:
                if event.get('symbol') == symbol:
                    result.append(event)
    
    return result

# 測試
if __name__ == "__main__":
    ensure_calendar_file()
    print("✅ 行事曆模塊已初始化")
    print(json.dumps(load_events(), ensure_ascii=False, indent=2))
