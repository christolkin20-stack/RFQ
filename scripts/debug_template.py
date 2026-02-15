
import os
import sys
import django

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "rfq_django.settings") # Adjust if settings name differs
django.setup()

from django.conf import settings
from django.template import Context, Template
from django.template.loader import get_template
from django.http import HttpRequest
from rfq.models import SupplierAccess, Project
from django.utils import timezone

# Mock Data
def run():
    print("--- Starting Template Validation ---")
    
    # Create Mock Objects (in memory only if not saving, but we might need to save for foreign keys)
    # We will try to just instantiate them without saving if possible, or fetch existing.
    
    try:
        # Try to get first existing access
        access = SupplierAccess.objects.first()
        if not access:
            print("No SupplierAccess found in DB. Creating mock...")
            p = Project(name="Test Project", data={})
            p.save()
            access = SupplierAccess(
                id="test_token_" + str(timezone.now().timestamp()),
                project=p,
                supplier_name="Test Supplier",
                requested_items=[
                    {
                        "id": "item1",
                        "item_drawing_no": "DWG-001",
                        "description": "Test Item",
                        "qty_1": 100,
                        "uom": "pcs",
                        "price": 0,
                        "mpn": "MPN-123"
                    }
                ],
                status="sent"
            )
            access.save()
            
        print(f"Using Access: {access.id}, Project: {access.project.name}")
        
        # Context
        context = {
            'access': access,
            'token': access.id,
            'items': access.requested_items,
            'project': access.project,
            'supplier_name': access.supplier_name,
            # 'error_message': 'Test Error' # Uncomment to test error display
        }
        
        # Load Template
        print("Loading template: rfq/supplier_portal.html")
        tmpl = get_template('rfq/supplier_portal.html')
        print(f"Loaded template from: {tmpl.template.origin.name}")
        
        # Render
        print("Rendering template...")
        rendered = tmpl.render(context)
        
        print("--- RENDER SUCCESS ---")
        print(f"Output length: {len(rendered)} chars")
        # print(rendered[:500]) # Preview
        
    except Exception as e:
        import traceback
        err_msg = str(e) + "\n" + traceback.format_exc()
        if hasattr(e, 'django_template_source'):
             try:
                 src = e.django_template_source
                 err_msg += f"\n\nTEMPLATE SOURCE: {src[0].name if src else 'Unknown'}"
             except:
                 pass
        print("\n!!! TEMPLATE ERROR !!!")
        print(err_msg)
        with open('scripts/debug_error.log', 'w', encoding='utf-8') as f:
            f.write(err_msg)

if __name__ == '__main__':
    run()
