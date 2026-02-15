import os, django, json
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rfq_django.settings')
django.setup()
from rfq.models import SupplierAccess

try:
    # Get the most recently submitted access
    access = SupplierAccess.objects.filter(status='submitted').order_by('-submitted_at').first()
    
    if not access:
        print("No submitted access found.")
    else:
        print('--- Verified Submission Data ---')
        print('ID: {}'.format(access.id))
        print('Submitted At: {}'.format(access.submitted_at))
        
        sub_items = access.submission_data.get('items', [])
        print('Items Count: {}'.format(len(sub_items)))
        
        if sub_items:
            # Print keys cleanly
            print('Submission Item Keys: {}'.format(list(sub_items[0].keys())))
            
            # Check if our target keys exist
            has_dn = 'item_drawing_no' in sub_items[0]
            has_mpn = 'mpn' in sub_items[0]
            print('Has item_drawing_no: {}'.format(has_dn))
            print('Has mpn: {}'.format(has_mpn))
            
            if has_dn:
                 print('Value of item_drawing_no: {}'.format(sub_items[0]['item_drawing_no']))


except Exception as e:
    print('Error: {}'.format(e))
