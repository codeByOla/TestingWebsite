from django.urls import path
from django.contrib.auth.views import LogoutView
from . import views
from .views import *
from django.contrib.auth import views as auth_views


urlpatterns = [
    path('student/register/', register_student, name='register_student'),
    path('student/login/', student_login, name='student_login'),
    path('admin/login/', admin_login, name='admin_login'),
    path('controller/login/', controller_login, name='controller_login'),
    path('logout/', logout_view, name='logout'),
    path('reset-password/', reset_password_request, name='reset_password_request'),
    path('reset-password-confirm/<uuid:token>/', reset_password_confirm, name='reset_password_confirm'),
]
