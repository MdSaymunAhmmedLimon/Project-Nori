from django.db import migrations


def seed(apps, schema_editor):
    Category = apps.get_model("accounts", "PositionCategory")
    permissions = [
        "accounts.view_operations", "accounts.manage_positions", "accounts.view_user", "accounts.change_user",
        "accounts.send_notifications", "rides.view_ride", "accounts.cancel_operational_ride",
        "rides.view_sosalert", "rides.change_sosalert", "rides.view_lostitem", "rides.change_lostitem",
        "drivers.view_driverprofile", "drivers.change_driverprofile", "drivers.view_driverdocument",
        "drivers.change_driverdocument", "drivers.view_payoutrequest", "drivers.change_payoutrequest",
    ]
    for name, dashboard, description, powers in [
        ("Administrator", "ADMIN", "Manage operations, people, invitations and final approvals.", permissions),
        ("Moderator", "MODERATOR", "Follow up on safety, lost items and trip cancellations.", ["rides.view_ride", "accounts.cancel_operational_ride", "rides.view_sosalert", "rides.change_sosalert", "rides.view_lostitem", "rides.change_lostitem", "accounts.send_notifications"]),
        ("Driver", "DRIVER", "Drive after invitation, acceptance and final license, vehicle and document review.", []),
    ]:
        Category.objects.get_or_create(name=name, defaults={"dashboard": dashboard, "description": description, "permissions": powers})


class Migration(migrations.Migration):
    dependencies = [("accounts", "0005_positioncategory_positioninvitation_positionevent_and_more")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
