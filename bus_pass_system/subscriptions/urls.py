from django.urls import path
from .views import *

urlpatterns = [
    # Student
    path("student/apply/",            apply_subscription, name="apply_subscription"),
    path("student/my-subscription/",  my_subscription,    name="my_subscription"),
    path("student/refresh-qr/",       refresh_qr,         name="refresh_qr"),
    # Admin
    path("admin/import-students/",              import_students,  name="import_students"),
    path("admin/students/create/",              create_student,   name="create_student"),
    path("admin/students/<int:pk>/edit/",       update_student,   name="update_student"),
    path("admin/students/<int:pk>/delete/",     delete_student,   name="delete_student"),
    path("admin/students/<int:pk>/revoke/",     revoke_student,   name="revoke_student"),
    path("admin/students/<int:pk>/restore/",    restore_student,  name="restore_student"),
    # Controller
    path("controller/logs/",    controller_logs,      name="controller_logs"),
    path("controller/verify/",  verify_subscription,  name="verify_subscription"),
    
]