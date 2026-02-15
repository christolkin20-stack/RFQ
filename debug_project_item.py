import os, django, json
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rfq_django.settings')
django.setup()
from rfq.models import SupplierAccess

try:
    access = SupplierAccess.objects.filter(status='submitted').order_by('-submitted_at').first()
    if access:
        p_items = access.project.data.get('items', [])
        print("Searching for 160-00110 in {} project items...".format(len(p_items)))
        
        found = False
        for pi in p_items:
            dn = str(pi.get('item_drawing_no') or pi.get('drawing_no') or '')
            mpn = str(pi.get('mpn') or '')
            
            if '160-00110' in dn or '160-00110' in mpn:
                print("FOUND POTENTIAL MATCH:")
                print("ID: " + str(pi.get('id')))
                print("Drawing No: " + dn)
                print("MPN: " + mpn)
                print("Normalized DN: " + dn.strip().lower())
                found = True
                
        if not found:
            print("NO MATCH FOUND IN PROJECT DATA.")
            # Print first 5 items to see what they look like
            for i, pi in enumerate(p_items[:5]):
                print("Item {}: DN={}, MPN={}".format(i, pi.get('item_drawing_no'), pi.get('mpn')))

except Exception as e:
    print(e)
