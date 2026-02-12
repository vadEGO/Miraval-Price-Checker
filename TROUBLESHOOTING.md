# Troubleshooting Guide

## Issue: No Results Found in UI

### Why This Happens

1. **Selenium is Slow**: Each website search takes 10-15 seconds with Selenium
2. **CAPTCHA Protection**: Sites detect automated browsing and show CAPTCHA pages
3. **Request Blocking**: Sites return 403 Forbidden for direct HTTP requests
4. **Timeout**: UI may timeout before search completes (searches can take 60+ seconds)

### Solutions

#### Option 1: Wait Longer
- Searches can take 60-90 seconds to complete
- Keep the browser open and wait
- Check browser console (F12) for progress

#### Option 2: Check Server Logs
```bash
tail -f /tmp/flask_app.log
```

#### Option 3: Test API Directly
```bash
curl -X POST http://localhost:5001/api/search \
  -H "Content-Type: application/json" \
  -d '{"wine_name":"Miraval"}' \
  --max-time 120
```

#### Option 4: Reduce Sites Searched
Edit `config.json` to search fewer sites:
```json
{
  "wine_sites": ["bws.com.au", "liquorland.com.au"]
}
```

### Current Status

- ✅ Server running on http://localhost:5001
- ✅ API endpoints working
- ✅ Selenium initialized
- ⚠️ Searches take 30-60+ seconds
- ⚠️ Some sites show CAPTCHA pages

### Next Steps

1. Open http://localhost:5001
2. Enter "Miraval" and click Search
3. **Wait 60-90 seconds** - don't close the browser
4. Results should appear when search completes
