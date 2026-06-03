from pyotp import random_base32

token = random_base32(128)
print(token)
