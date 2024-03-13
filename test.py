from ssl_checker import check_ssl_expiry 
# Check SSL certificate expiry
website_url="https://qtest.techfliq.com/"
expiry_date = check_ssl_expiry(website_url)  # Call function to check SSL certificate expiry
print(expiry_date)
