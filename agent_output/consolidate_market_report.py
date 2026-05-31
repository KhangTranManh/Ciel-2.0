import re
import os

def parse_market_alerts(file_path):
    """
    Parses the market alert file to extract product names and their prices.
    Expected format: "Product Name: $Price"
    """
    market_prices = {}
    try:
        with open(file_path, 'r') as f:
            for line in f:
                match = re.match(r"^(.*?):\s*\$([\d.]+)", line.strip())
                if match:
                    product_name = match.group(1).strip()
                    price = float(match.group(2))
                    market_prices[product_name] = price
    except FileNotFoundError:
        print(f"Error: Market alert file not found at {file_path}")
    return market_prices

def parse_email_recommendations(file_path):
    """
    Parses the sorted emails file to extract product recommendations and prices.
    Expected format: Blocks separated by '---', each with 'Subject:' and 'Body:'.
    Product and price expected in Body: "Product [Name] at $[Price]"
    """
    email_recommendations = {}
    try:
        with open(file_path, 'r') as f:
            content = f.read()
            email_blocks = content.strip().split('---')

            for block in email_blocks:
                if not block.strip():
                    continue

                # Extract the full block for the recommendation text
                full_recommendation_text = block.strip()

                # Try to find product name and recommended price within the block
                # This regex looks for "Product [Name] at/for $[Price]"
                match = re.search(r"Product\s+([A-Za-z0-9\s]+?)\s+(?:at|for)\s+\$([\d.]+)", full_recommendation_text, re.IGNORECASE)
                if match:
                    product_name = match.group(1).strip()
                    recommended_price = float(match.group(2))
                    email_recommendations[product_name] = (recommended_price, full_recommendation_text)
    except FileNotFoundError:
        print(f"Error: Sorted emails file not found at {file_path}")
    return email_recommendations

def generate_evening_report(market_alerts_path, emails_path, report_output_path):
    """
    Generates a consolidated report by cross-referencing market alerts and email recommendations.
    Identifies price deviations greater than 0.5%.
    """
    market_prices = parse_market_alerts(market_alerts_path)
    email_recs = parse_email_recommendations(emails_path)

    report_lines = ["EVENING MARKET DEVIATION REPORT\n"]
    found_deviations = False

    for product_name, market_price in market_prices.items():
        if product_name in email_recs:
            recommended_price, full_recommendation = email_recs[product_name]

            if recommended_price == 0: # Avoid division by zero
                continue

            deviation = ((market_price - recommended_price) / recommended_price) * 100

            if abs(deviation) > 0.5:
                found_deviations = True
                report_lines.append("-" * 80)
                report_lines.append(f"Product: {product_name}")
                report_lines.append(f"Market Price: ${market_price:.2f}")
                report_lines.append(f"Recommended Price: ${recommended_price:.2f}")
                report_lines.append(f"Deviation: {deviation:+.2f}%")
                report_lines.append("Recommendation:")
                # Indent the recommendation text for better readability
                indented_recommendation = "\n".join([f"  {line}" for line in full_recommendation.splitlines()])
                report_lines.append(indented_recommendation)
                report_lines.append("-" * 80 + "\n")

    if not found_deviations:
        report_lines.append("No significant price deviations (> 0.5%) found between market alerts and email recommendations.")

    # Ensure the output directory exists
    output_dir = os.path.dirname(report_output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)

    with open(report_output_path, 'w') as f:
        f.write("\n".join(report_lines))

    return "\n".join(report_lines)

def read_and_print_report(report_path):
    """
    Reads the generated report file and prints its content to the console.
    """
    try:
        with open(report_path, 'r') as f:
            print(f.read())
    except FileNotFoundError:
        print(f"Error: Report file not found at {report_path}")

if __name__ == "__main__":
    MARKET_ALERTS_FILE = 'evening_market_alert.txt'
    SORTED_EMAILS_FILE = 'autonomous_pipeline/agent_output/sorted_emails.txt'
    EVENING_REPORT_FILE = 'autonomous_pipeline/agent_output/evening_report.txt'

    # Create dummy files for demonstration if they don't exist
    # This part is for making the script runnable out-of-the-box for testing
    # In a real scenario, these files would be pre-existing.
    if not os.path.exists(MARKET_ALERTS_FILE):
        with open(MARKET_ALERTS_FILE, 'w') as f:
            f.write("Product A: $100.00\n")
            f.write("Product B: $50.50\n")
            f.write("Product C: $200.00\n")
            f.write("Product E: $75.00\n") # No email for E
            f.write("Product F: $10.00\n") # Small deviation
    
    output_dir = os.path.dirname(SORTED_EMAILS_FILE)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)

    if not os.path.exists(SORTED_EMAILS_FILE):
        with open(SORTED_EMAILS_FILE, 'w') as f:
            f.write("Subject: Recommendation for Product A\n")
            f.write("Body: We recommend buying Product A at $99.00 for optimal profit.\n")
            f.write("---\n")
            f.write("Subject: Update on Product B\n")
            f.write("Body: Consider selling Product B at $50.00 based on recent trends.\n")
            f.write("---\n")
            f.write("Subject: Product D Opportunity\n")
            f.write("Body: New opportunity for Product D at $150.00.\n") # No market alert for D
            f.write("---\n")
            f.write("Subject: Product C Stable\n")
            f.write("Body: Product C is stable at $200.10, no action needed.\n") # Small deviation
            f.write("---\n")
            f.write("Subject: Product F Alert\n")
            f.write("Body: Watch Product F, recommended price is $9.98.\n") # Small deviation

    # Generate the report
    print(f"Generating report to {EVENING_REPORT_FILE}...")
    generate_evening_report(MARKET_ALERTS_FILE, SORTED_EMAILS_FILE, EVENING_REPORT_FILE)
    print("Report generated successfully.\n")

    # Read and print the report to console
    print("--- Content of the generated report ---")
    read_and_print_report(EVENING_REPORT_FILE)
    print("--- End of report content ---")