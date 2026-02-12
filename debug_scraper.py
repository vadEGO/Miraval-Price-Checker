#!/usr/bin/env python3
"""
Debug script to inspect what's being returned from wine sites
"""

import sys
from wine_scraper import WineScraper

def debug_site(scraper, site_name, wine_name="shiraz"):
    """Debug a specific site"""
    print(f"\n{'='*60}")
    print(f"Debugging {site_name}")
    print(f"{'='*60}\n")
    
    site_handlers = {
        'danmurphys.com.au': scraper.search_dan_murphys,
        'bws.com.au': scraper.search_bws,
        'liquorland.com.au': scraper.search_liquorland,
        'firstchoice.com.au': scraper.search_first_choice,
    }
    
    if site_name not in site_handlers:
        print(f"Unknown site: {site_name}")
        return
    
    handler = site_handlers[site_name]
    
    # Get page content
    search_url = f"https://www.{site_name}/search?q={wine_name}"
    print(f"URL: {search_url}")
    print(f"Using Selenium: {scraper.use_selenium}")
    
    page_content = scraper._get_page_content(search_url)
    
    if not page_content:
        print("❌ No page content returned!")
        return
    
    print(f"✓ Got page content ({len(page_content)} characters)")
    
    # Save HTML for inspection
    filename = f"debug_{site_name.replace('.', '_')}.html"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(page_content)
    print(f"✓ Saved HTML to {filename}")
    
    # Try to find products
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(page_content, 'html.parser')
    
    # Look for common patterns
    print("\nSearching for product patterns...")
    
    patterns = [
        ('divs with product class', soup.find_all('div', class_=lambda x: x and 'product' in str(x).lower())),
        ('articles', soup.find_all('article')),
        ('items with data-product-id', soup.find_all(attrs={'data-product-id': True})),
        ('elements with $ in text', [e for e in soup.find_all(['div', 'li', 'article']) if '$' in e.get_text()[:100]]),
    ]
    
    for name, elements in patterns:
        print(f"  {name}: {len(elements)} found")
        if elements:
            print(f"    First element text preview: {elements[0].get_text()[:100]}...")
    
    # Try the actual search
    print(f"\nRunning search for '{wine_name}'...")
    results = handler(wine_name)
    print(f"Results found: {len(results)}")
    for i, result in enumerate(results, 1):
        print(f"  {i}. {result}")

if __name__ == "__main__":
    wine_name = sys.argv[1] if len(sys.argv) > 1 else "shiraz"
    site = sys.argv[2] if len(sys.argv) > 2 else "liquorland.com.au"
    
    print(f"Testing with wine: '{wine_name}' on site: {site}")
    
    scraper = WineScraper('config.json', use_selenium=True)
    debug_site(scraper, site, wine_name)
    
    if scraper.driver:
        scraper.driver.quit()
