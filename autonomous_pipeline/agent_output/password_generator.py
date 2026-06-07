import random
import string

def generate_secure_password():
    """
    Generates a secure, random 12-character password.
    The password contains at least one uppercase letter, one lowercase letter,
    one digit, and one special character.
    """
    length = 12

    # Define character sets
    lowercase_chars = string.ascii_lowercase
    uppercase_chars = string.ascii_uppercase
    digit_chars = string.digits
    special_chars = string.punctuation

    # Ensure the password contains at least one of each required type
    password_chars = [
        random.choice(lowercase_chars),
        random.choice(uppercase_chars),
        random.choice(digit_chars),
        random.choice(special_chars)
    ]

    # Combine all character sets for the remaining characters
    all_chars = lowercase_chars + uppercase_chars + digit_chars + special_chars

    # Fill the remaining characters
    for _ in range(length - len(password_chars)):
        password_chars.append(random.choice(all_chars))

    # Shuffle the list to ensure random placement of required characters
    random.shuffle(password_chars)

    # Join the characters to form the final password string
    return "".join(password_chars)

if __name__ == "__main__":
    password = generate_secure_password()
    print(password)