from datetime import datetime, timezone, timedelta
dt = datetime.fromtimestamp(1779845400, timezone.utc) + timedelta(hours=8)
print(dt.strftime('%Y-%m-%d %H:%M Shanghai'))
