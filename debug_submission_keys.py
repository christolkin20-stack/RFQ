import os, django, json
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rfq_django.settings')
django.setup()
from rfq.models import SupplierAccess

try:
    access = SupplierAccess.objects.filter(status='submitted').order_by('-submitted_at').first()
    if access:
        sub_items = access.submission_data.get('items', [])
        if sub_items:
            print("KEYS_START")
            for k in sub_items[0].keys():
                print(k)
            print("KEYS_END")
            
            # Print value of drawing no if it exists
            if 'item_drawing_no' in sub_items[0]:
                 print("DN_VALUE: " + str(sub_items[0]['item_drawing_no']))
            else:
                 print("DN_VALUE: MISSING")

except Exception as e:
    print(e)
