{{/* Common helpers — populated as templates land in W11 */}}

{{- define "kubepilot.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "kubepilot.fullname" -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "kubepilot.labels" -}}
app.kubernetes.io/name: {{ include "kubepilot.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end -}}

{{/* Postgres connection URL: bundled in-cluster service or the operator's externalUrl. */}}
{{- define "kubepilot.postgres.url" -}}
{{- if .Values.postgres.enabled -}}
{{- $host := printf "%s-postgres.%s.svc.cluster.local" (include "kubepilot.fullname" .) .Release.Namespace -}}
{{- printf "postgresql://%s:%s@%s:5432/%s" .Values.postgres.auth.username .Values.postgres.auth.password $host .Values.postgres.auth.database -}}
{{- else -}}
{{- required "postgres.enabled=false requires postgres.externalUrl" .Values.postgres.externalUrl -}}
{{- end -}}
{{- end -}}

{{/* Redis connection URL: bundled in-cluster service or the operator's externalUrl. */}}
{{- define "kubepilot.redis.url" -}}
{{- if .Values.redis.enabled -}}
{{- printf "redis://%s-redis.%s.svc.cluster.local:6379/0" (include "kubepilot.fullname" .) .Release.Namespace -}}
{{- else -}}
{{- required "redis.enabled=false requires redis.externalUrl" .Values.redis.externalUrl -}}
{{- end -}}
{{- end -}}

{{/* Name of the Secret holding LLM credentials (existing or rendered). */}}
{{- define "kubepilot.llm.secretName" -}}
{{- if .Values.llm.existingSecret -}}{{ .Values.llm.existingSecret }}{{- else -}}{{ include "kubepilot.fullname" . }}-llm-credentials{{- end -}}
{{- end -}}

{{/* Name of the Secret holding the API gateway auth key (existing or rendered). */}}
{{- define "kubepilot.apiAuth.secretName" -}}
{{- if .Values.apiGateway.auth.existingSecret -}}{{ .Values.apiGateway.auth.existingSecret }}{{- else -}}{{ include "kubepilot.fullname" . }}-api-auth{{- end -}}
{{- end -}}
