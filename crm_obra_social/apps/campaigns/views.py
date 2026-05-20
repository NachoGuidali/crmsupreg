import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import ListView, DetailView, DeleteView
from django.urls import reverse_lazy

from .forms import CampanaForm
from .models import Campana
from .tasks import ejecutar_campana


class ContactoBuscarView(LoginRequiredMixin, View):
    """AJAX: search contacts/leads for campaign manual selection."""

    def get(self, request):
        q = request.GET.get('q', '').strip()
        page = int(request.GET.get('page', 1))
        from apps.leads.models import Lead
        from django.db.models import Q
        qs = Lead.objects.filter(telefono__startswith='+').select_related('plan_interes')
        if q:
            qs = qs.filter(
                Q(nombre_completo__icontains=q) |
                Q(telefono__icontains=q) |
                Q(email__icontains=q)
            )
        from django.core.paginator import Paginator
        paginator = Paginator(qs.order_by('nombre_completo'), 20)
        pg = paginator.get_page(page)
        results = [
            {
                'id': l.pk,
                'nombre': l.nombre_completo,
                'telefono': l.telefono,
                'email': l.email or '',
                'plan': str(l.plan_interes) if l.plan_interes else '',
                'estado': l.get_estado_display(),
            }
            for l in pg
        ]
        return JsonResponse({
            'results': results,
            'has_next': pg.has_next(),
            'total': paginator.count,
        })


def _get_extra_keys_json():
    """Return JSON array of all datos_extra keys found across leads."""
    from apps.leads.models import Lead
    keys = set()
    for lead in Lead.objects.exclude(datos_extra={}).values_list('datos_extra', flat=True):
        if isinstance(lead, dict):
            keys.update(lead.keys())
    return json.dumps(sorted(keys))


class SupervisorRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.can_see_all_leads


class CampanaListView(LoginRequiredMixin, SupervisorRequiredMixin, ListView):
    model = Campana
    template_name = 'campaigns/campana_list.html'
    context_object_name = 'campanas'
    paginate_by = 25


class CampanaCreateView(LoginRequiredMixin, SupervisorRequiredMixin, View):
    template_name = 'campaigns/campana_form.html'

    def _plantillas_data(self):
        from apps.whatsapp.models import PlantillaHSM
        data = {}
        for p in PlantillaHSM.objects.filter(activa=True, status=PlantillaHSM.STATUS_APROBADA):
            data[str(p.pk)] = {
                'nombre': p.nombre,
                'cuerpoRaw': p.cuerpo,
                'variables': p.variables or [],
                'header_tipo': p.header_tipo,
                'header_contenido': p.header_contenido,
                'footer': p.footer,
                'botones': p.botones or [],
            }
        return data

    def _ctx(self, form):
        return {
            'form': form,
            'plantillas_data': self._plantillas_data(),
            'extra_keys_json': _get_extra_keys_json(),
        }

    def get(self, request):
        return render(request, self.template_name, self._ctx(CampanaForm()))

    def post(self, request):
        form = CampanaForm(request.POST)
        if form.is_valid():
            campana = form.save(commit=False)
            campana.creado_por = request.user
            campana.status = Campana.STATUS_BORRADOR
            campana.save()
            count = campana.get_segment_queryset().count()
            campana.total_destinatarios = count
            campana.save(update_fields=['total_destinatarios'])
            messages.success(request, f'Campaña creada. Destinatarios estimados: {count}.')
            return redirect('campaigns:detail', pk=campana.pk)
        return render(request, self.template_name, self._ctx(form))


class CampanaDetailView(LoginRequiredMixin, SupervisorRequiredMixin, DetailView):
    model = Campana
    template_name = 'campaigns/campana_detail.html'
    context_object_name = 'campana'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        logs = self.object.logs.select_related('lead').order_by('-created_at')
        paginator = Paginator(logs, 25)
        ctx['logs'] = paginator.get_page(self.request.GET.get('page'))
        ctx['destinatarios_preview'] = self.object.get_segment_queryset().count()
        return ctx


class CampanaLanzarView(LoginRequiredMixin, SupervisorRequiredMixin, View):
    def post(self, request, pk):
        campana = get_object_or_404(Campana, pk=pk, status__in=[Campana.STATUS_BORRADOR, Campana.STATUS_PROGRAMADA])
        campana.status = Campana.STATUS_PROGRAMADA
        campana.save(update_fields=['status'])
        ejecutar_campana.delay(campana.pk)
        messages.success(request, f'Campaña "{campana.nombre}" lanzada. Se enviará en segundo plano.')
        return redirect('campaigns:detail', pk=pk)


class CampanaDeleteView(LoginRequiredMixin, SupervisorRequiredMixin, DeleteView):
    model = Campana
    template_name = 'campaigns/campana_confirm_delete.html'
    success_url = reverse_lazy('campaigns:list')

    def get_queryset(self):
        return super().get_queryset().filter(status=Campana.STATUS_BORRADOR)
