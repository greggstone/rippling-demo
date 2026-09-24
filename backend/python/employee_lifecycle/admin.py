from django.contrib import admin

from .models import Employee, RoleChangeRequest, WorkflowRun, WorkflowStepRun


class WorkflowStepRunInline(admin.TabularInline):
    model = WorkflowStepRun
    extra = 0
    readonly_fields = ("name", "sequence", "status", "attempts", "last_error", "result")


@admin.register(WorkflowRun)
class WorkflowRunAdmin(admin.ModelAdmin):
    list_display = ("id", "request", "status", "current_step", "attempt", "updated_at")
    list_filter = ("status",)
    inlines = [WorkflowStepRunInline]


admin.site.register(Employee)
admin.site.register(RoleChangeRequest)
