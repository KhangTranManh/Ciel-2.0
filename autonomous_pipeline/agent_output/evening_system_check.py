import re
import subprocess
import os

def validate_crypto_data(file_path):
    """
    Reads the crypto data file, extracts High, Low, and 24h % Change values
    for BTC, ETH, and SOL, and validates if they are numeric.
    """
    validation_results = []
    target_cryptos = ['BTC', 'ETH', 'SOL']
    # Initialize a dictionary to store extracted data for target cryptos
    # Values are None initially, indicating data not yet found or extracted.
    crypto_data_found = {crypto: {'high': None, 'low': None, 'change': None} for crypto in target_cryptos}

    try:
        with open(file_path, 'r') as f:
            for line in f:
                line = line.strip()
                for crypto in target_cryptos:
                    # Check if the line starts with the crypto symbol followed by a space
                    # This helps to identify the relevant data line for each crypto.
                    if line.startswith(crypto + ' '):
                        # Regex to extract High, Low, and 24h % Change values.
                        # It's designed to be permissive, capturing any non-space characters
                        # after the labels, allowing for non-numeric values like 'N/A'
                        # which will then be caught by the numeric validation step.
                        # The '%' sign for change is optional in the capture group.
                        match = re.search(
                            rf'{crypto}\s+'
                            r'High:\s*([^ ]+)\s+'  # Captures value for High
                            r'Low:\s*([^ ]+)\s+'   # Captures value for Low
                            r'24h\s*%\s*Change:\s*([^% ]+)%?' # Captures value for 24h % Change, optional %
                            , line
                        )
                        if match:
                            crypto_data_found[crypto]['high'] = match.group(1)
                            crypto_data_found[crypto]['low'] = match.group(2)
                            crypto_data_found[crypto]['change'] = match.group(3)
                        break # Move to the next line in the file once a crypto entry is processed
    except FileNotFoundError:
        validation_results.append(f"Error: Crypto data file '{file_path}' not found.")
        return validation_results
    except Exception as e:
        validation_results.append(f"Error reading or parsing crypto data file '{file_path}': {e}")
        return validation_results

    validation_results.append("--- Crypto Data Verification ---")
    for crypto in target_cryptos:
        data = crypto_data_found[crypto]
        high_val = data['high']
        low_val = data['low']
        change_val = data['change']

        # If all values are None, it means no data entry was found for this crypto
        if high_val is None and low_val is None and change_val is None:
            validation_results.append(f"  {crypto}: Data entry not found in file.")
            continue

        high_valid = False
        low_valid = False
        change_valid = False

        # Attempt to convert values to float to check if they are numeric
        try:
            float(high_val)
            high_valid = True
        except (ValueError, TypeError): # TypeError handles cases where high_val is None
            pass

        try:
            float(low_val)
            low_valid = True
        except (ValueError, TypeError):
            pass

        try:
            float(change_val)
            change_valid = True
        except (ValueError, TypeError):
            pass

        validation_results.append(f"  {crypto}:")
        validation_results.append(f"    High: {high_val if high_val is not None else 'N/A'} - {'Valid' if high_valid else 'Invalid (Not numeric)'}")
        validation_results.append(f"    Low: {low_val if low_val is not None else 'N/A'} - {'Valid' if low_valid else 'Invalid (Not numeric)'}")
        validation_results.append(f"    24h % Change: {change_val if change_val is not None else 'N/A'}% - {'Valid' if change_valid else 'Invalid (Not numeric)'}")
        validation_results.append("") # Add a blank line for readability

    return validation_results

def execute_git_status(repo_path):
    """
    Executes 'git status' on the specified repository path and captures its output.
    """
    git_report = []
    git_report.append(f"--- Git Status Report for '{repo_path}' ---")

    # Check if the repository path exists and is a directory
    if not os.path.isdir(repo_path):
        git_report.append(f"Error: Repository path '{repo_path}' does not exist or is not a directory.")
        return git_report

    try:
        # First, check if the directory is actually a Git repository
        subprocess.run(['git', 'rev-parse', '--is-inside-work-tree'], cwd=repo_path, check=True,
                       capture_output=True, text=True, timeout=10)
    except subprocess.CalledProcessError:
        git_report.append(f"Error: '{repo_path}' is not a Git repository.")
        return git_report
    except FileNotFoundError:
        git_report.append("Error: 'git' command not found. Please ensure Git is installed and in your PATH.")
        return git_report
    except subprocess.TimeoutExpired:
        git_report.append(f"Error: 'git rev-parse' command timed out for '{repo_path}'.")
        return git_report
    except Exception as e:
        git_report.append(f"An unexpected error occurred while checking git repository status: {e}")
        return git_report

    try:
        # Execute 'git status' command
        result = subprocess.run(
            ['git', 'status'],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True, # Raise a CalledProcessError for non-zero exit codes
            timeout=30 # Set a timeout for the git status command
        )
        git_report.append(result.stdout.strip())
    except subprocess.CalledProcessError as e:
        git_report.append(f"Error executing 'git status': Command returned non-zero exit code {e.returncode}.")
        git_report.append(f"Stderr: {e.stderr.strip()}")
    except FileNotFoundError:
        git_report.append("Error: 'git' command not found. Please ensure Git is installed and in your PATH.")
    except subprocess.TimeoutExpired:
        git_report.append(f"Error: 'git status' command timed out for '{repo_path}'.")
    except Exception as e:
        git_report.append(f"An unexpected error occurred during git status: {e}")

    return git_report

def main():
    """
    Main function to orchestrate the crypto data validation and git status execution,
    then prints the combined report.
    """
    crypto_file_path = 'autonomous_pipeline/agent_output/crypto_24h_combined.txt'
    git_repository_path = 'D:/Ciel 2.0/'

    combined_report = []

    # Get crypto data validation results
    combined_report.extend(validate_crypto_data(crypto_file_path))
    combined_report.append("\n") # Add a blank line for separation

    # Get git status report
    combined_report.extend(execute_git_status(git_repository_path))

    # Print the final combined report
    print("\n".join(combined_report))

if __name__ == "__main__":
    main()