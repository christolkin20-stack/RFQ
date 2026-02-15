import os, django, json
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'rfq_django.settings')
django.setup()
from rfq.models import SupplierAccess, Project

try:
    access = SupplierAccess.objects.latest('submitted_at')
    print('--- SupplierAccess ---')
    print('Token: {}'.format(access.id))
    
    sub_items = access.submission_data.get('items', [])
    print('Submission Items Count: {}'.format(len(sub_items)))
    
    print('\n--- Project Items ---')
    project = access.project
    pitems = project.data.get('items', [])
    print('Project ID: {}'.format(project.id))
    print('Project Items Count: {}'.format(len(pitems)))
            
    # Check for matches (Robust)
    print('\n--- Robust Matching Test ---')
    for si in sub_items:
        sid = str(si.get('id') or '')
        sdn = str(si.get('item_drawing_no') or si.get('drawing_no') or '').strip().lower()
        smpn = str(si.get('mpn') or '').strip().lower()
        
        found = False
        match_type = 'None'
        
        for pi in pitems:
            pid = str(pi.get('id') or '')
            pdn = str(pi.get('item_drawing_no') or pi.get('drawing_no') or '').strip().lower()
            pmpn = str(pi.get('mpn') or '').strip().lower()
            
            if pid == sid and pid:
                match_type = 'ID'
                found = True
                break
            # Logic: If ID fails, check Drawing No.
            # Only use DN if it exists.
            if sdn and pdn == sdn:
                 match_type = 'DrawingNo'
                 found = True
                 break
            # Logic: If DN missing/fails, check MPN (dangerous but last resort)
            if not sdn and smpn and pmpn == smpn:
                 match_type = 'MPN'
                 found = True
                 break
                 
        if found:
            print('  MATCH FOUND for item {}: Type={}'.format(sid[:10], match_type))
        else:
            print('  NO MATCH FOR item {}: dn={}, mpn={}'.format(sid[:10], sdn, smpn))
            
except Exception as e:
    print('Error: {}'.format(e))
