{{- define "finops.name" -}}finops{{- end -}}

{{- define "finops.labels" -}}
app.kubernetes.io/name: {{ include "finops.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "finops.selectorLabels" -}}
app.kubernetes.io/name: {{ include "finops.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/* Common env block shared by Deployment + seed Job. */}}
{{- define "finops.env" -}}
- name: FOCUS_PG_MODE
  value: {{ .Values.db.mode | quote }}
- name: FOCUS_PG_HOST
  value: {{ .Values.db.host | quote }}
- name: FOCUS_PG_PORT
  value: {{ .Values.db.port | quote }}
- name: FOCUS_PG_USER
  value: {{ .Values.db.user | quote }}
- name: FOCUS_PG_DB
  value: {{ .Values.db.name | quote }}
- name: FOCUS_PG_PASS
  valueFrom:
    secretKeyRef:
      name: {{ .Values.db.passwordSecretName | quote }}
      key: {{ .Values.db.passwordSecretKey | quote }}
- name: BEDROCK_DISABLED
  value: {{ .Values.bedrock.disabled | quote }}
- name: BEDROCK_REGION
  value: {{ .Values.bedrock.region | quote }}
- name: BEDROCK_MODEL_ID
  value: {{ .Values.bedrock.modelId | quote }}
{{- end -}}
