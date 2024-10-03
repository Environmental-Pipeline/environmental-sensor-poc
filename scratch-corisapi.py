from CorisAPI import CorisAPI

api = CorisAPI(CatsUserID = 2496)
current = api.get_current_status()
history = api.get_historical_status(days_back = 90, testing = True)

