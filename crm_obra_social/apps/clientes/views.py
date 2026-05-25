from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import ListView, DetailView, DeleteView
from django.urls import reverse_lazy

from apps.leads.models import CampoPersonalizado
from .forms import ClienteForm
from .models import Cliente


class ClienteListView(LoginRequiredMixin, ListView):
    model = Cliente
    template_name = 'clientes/cliente_list.html'
    context_object_name = 'clientes'
    paginate_by = 25

    def get_queryset(self):
        qs = Cliente.objects.select_related('plan', 'agente')
        q = self.request.GET.get('q', '').strip()
        if q:
            from django.db.models import Q
            qs = qs.filter(
                Q(nombre_completo__icontains=q) |
                Q(dni__icontains=q) |
                Q(telefono__icontains=q) |
                Q(email__icontains=q) |
                Q(numero_afiliado__icontains=q)
            )
        plan_id = self.request.GET.get('plan', '')
        if plan_id:
            qs = qs.filter(plan_id=plan_id)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from apps.leads.models import Plan
        ctx['q'] = self.request.GET.get('q', '')
        ctx['plan_id'] = self.request.GET.get('plan', '')
        ctx['planes'] = Plan.objects.filter(activo=True)
        ctx['total'] = self.get_queryset().count()
        return ctx


class ClienteDetailView(LoginRequiredMixin, DetailView):
    model = Cliente
    template_name = 'clientes/cliente_detail.html'
    context_object_name = 'cliente'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['campos'] = CampoPersonalizado.objects.filter(
            activo=True,
            alcance__in=[CampoPersonalizado.ALCANCE_CLIENTES, CampoPersonalizado.ALCANCE_AMBOS]
        )
        return ctx

    def post(self, request, pk):
        cliente = get_object_or_404(Cliente, pk=pk)
        if request.POST.get('action') == 'save_campos':
            campos = CampoPersonalizado.objects.filter(
                activo=True,
                alcance__in=[CampoPersonalizado.ALCANCE_CLIENTES, CampoPersonalizado.ALCANCE_AMBOS]
            )
            extra = dict(cliente.datos_extra or {})
            for campo in campos:
                if campo.tipo == CampoPersonalizado.TIPO_BOOLEANO:
                    extra[campo.slug] = bool(request.POST.get(f'campo_{campo.slug}'))
                else:
                    val = request.POST.get(f'campo_{campo.slug}', '').strip()
                    if val:
                        extra[campo.slug] = val
                    else:
                        extra.pop(campo.slug, None)
            cliente.datos_extra = extra
            cliente.save(update_fields=['datos_extra'])
            messages.success(request, 'Campos guardados.')
        return redirect('clientes:detail', pk=pk)


class ClienteCreateView(LoginRequiredMixin, View):
    template_name = 'clientes/cliente_form.html'

    def get(self, request):
        return render(request, self.template_name, {'form': ClienteForm(user=request.user)})

    def post(self, request):
        form = ClienteForm(request.POST, user=request.user)
        if form.is_valid():
            cliente = form.save(commit=False)
            if not cliente.agente and not request.user.can_see_all_leads:
                cliente.agente = request.user
            cliente.save()
            messages.success(request, 'Cliente creado correctamente.')
            return redirect('clientes:detail', pk=cliente.pk)
        return render(request, self.template_name, {'form': form})


class ClienteUpdateView(LoginRequiredMixin, View):
    template_name = 'clientes/cliente_form.html'

    def get(self, request, pk):
        cliente = get_object_or_404(Cliente, pk=pk)
        return render(request, self.template_name, {'form': ClienteForm(instance=cliente, user=request.user), 'cliente': cliente})

    def post(self, request, pk):
        cliente = get_object_or_404(Cliente, pk=pk)
        form = ClienteForm(request.POST, instance=cliente, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Cliente actualizado.')
            return redirect('clientes:detail', pk=pk)
        return render(request, self.template_name, {'form': form, 'cliente': cliente})


class ClienteDeleteView(LoginRequiredMixin, DeleteView):
    model = Cliente
    template_name = 'clientes/cliente_confirm_delete.html'
    success_url = reverse_lazy('clientes:list')
