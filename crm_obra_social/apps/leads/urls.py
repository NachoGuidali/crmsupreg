from django.urls import path
from . import views

app_name = 'leads'

urlpatterns = [
    path('', views.LeadListView.as_view(), name='list'),
    path('kanban/', views.LeadKanbanView.as_view(), name='kanban'),
    path('nuevo/', views.LeadCreateView.as_view(), name='create'),
    path('<int:pk>/', views.LeadDetailView.as_view(), name='detail'),
    path('<int:pk>/editar/', views.LeadUpdateView.as_view(), name='update'),
    path('<int:pk>/eliminar/', views.LeadDeleteView.as_view(), name='delete'),
    path('<int:pk>/estado/', views.LeadEstadoChangeView.as_view(), name='change_estado'),
    path('<int:pk>/mover/', views.LeadKanbanMoveView.as_view(), name='kanban_move'),
    path('importar/', views.LeadCSVImportView.as_view(), name='csv_import'),
    path('importar/template/', views.LeadImportTemplateView.as_view(), name='import_template'),
    path('exportar/', views.LeadCSVExportView.as_view(), name='csv_export'),
]
