import pyotp
import segno

from app_secrets import LOGIN_KEY

otp = pyotp.TOTP(LOGIN_KEY)
s = otp.provisioning_uri(name="Admin", issuer_name="Mes Oraux")
print(otp.now())
qr = segno.make_qr(s)
qr.save("../key.png")
qr.terminal(compact=True)
test_code = input("Enter the code given by your app: ")
if otp.verify(test_code, valid_window=1):
    print('test successfull!')
else:
    print('Please try again, the test was unsuccessfull')

