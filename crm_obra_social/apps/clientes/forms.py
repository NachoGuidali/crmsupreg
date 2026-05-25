from django import forms
from apps.leads.models import Plan
from .models import Cliente


class ClienteForm(forms.ModelForm):
    class Meta:
        model = Cliente
        fields = [
            'nombre_completo', 'dni', 'fecha_nacimiento', 'telefono', 'email',
            'localidad', 'provincia', 'plan', 'numero_afiliado', 'grupo_familiar',
            'agente', 'notas',
        ]
        widgets = {
            'fecha_nacimiento': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'notas': forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if not isinstance(field.widget, (forms.Select, forms.Textarea, forms.DateInput)):
                field.widget.attrs['class'] = 'form-control'
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs['class'] = 'form-select'
        self.fields['plan'].queryset = Plan.objects.filter(activo=True)
        if user and not user.can_see_all_leads:
            self.fields.pop('agente', None)
