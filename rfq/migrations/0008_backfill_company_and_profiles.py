from django.conf import settings
from django.db import migrations


def forwards(apps, schema_editor):
    Company = apps.get_model('rfq', 'Company')
    Project = apps.get_model('rfq', 'Project')
    Attachment = apps.get_model('rfq', 'Attachment')
    SupplierAccess = apps.get_model('rfq', 'SupplierAccess')
    SupplierAccessRound = apps.get_model('rfq', 'SupplierAccessRound')
    SupplierInteractionFile = apps.get_model('rfq', 'SupplierInteractionFile')
    Quote = apps.get_model('rfq', 'Quote')
    UserCompanyProfile = apps.get_model('rfq', 'UserCompanyProfile')
    app_label, model_name = settings.AUTH_USER_MODEL.split('.')
    User = apps.get_model(app_label, model_name)

    default_company, _ = Company.objects.get_or_create(name='Default Company', defaults={'is_active': True})

    Project.objects.filter(company__isnull=True).update(company=default_company)

    for row in Attachment.objects.filter(company__isnull=True).select_related('project'):
        row.company_id = row.project.company_id if row.project_id else default_company.id
        row.save(update_fields=['company'])

    for row in SupplierAccess.objects.filter(company__isnull=True).select_related('project'):
        row.company_id = row.project.company_id if row.project_id else default_company.id
        row.save(update_fields=['company'])

    for row in SupplierAccessRound.objects.filter(company__isnull=True).select_related('supplier_access'):
        comp = None
        if row.supplier_access_id:
            comp = row.supplier_access.company_id
        row.company_id = comp or default_company.id
        row.save(update_fields=['company'])

    for row in SupplierInteractionFile.objects.filter(company__isnull=True).select_related('supplier_access'):
        comp = None
        if row.supplier_access_id:
            comp = row.supplier_access.company_id
        row.company_id = comp or default_company.id
        row.save(update_fields=['company'])

    for row in Quote.objects.filter(company__isnull=True).select_related('project'):
        comp = row.project.company_id if row.project_id else None
        row.company_id = comp or default_company.id
        row.save(update_fields=['company'])

    for user in User.objects.all():
        profile = UserCompanyProfile.objects.filter(user_id=user.id).first()
        if profile:
            changed = False
            if not profile.company_id and not (profile.role == 'superadmin'):
                profile.company_id = default_company.id
                changed = True
            if changed:
                profile.save(update_fields=['company'])
            continue

        role = 'superadmin' if user.is_superuser else 'viewer'
        company_id = None if role == 'superadmin' else default_company.id
        UserCompanyProfile.objects.create(
            user_id=user.id,
            company_id=company_id,
            role=role,
            is_management=bool(user.is_staff or user.is_superuser),
            is_active=True,
        )


def backwards(apps, schema_editor):
    # No destructive rollback for data backfill.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('rfq', '0007_company_alter_quote_source_auditlog_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
