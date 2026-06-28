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
{{- /* Tenant branding/currency (PKG-1). config/tenant.json is baked into the
       image; these env overrides let a deploy rebrand without rebuilding. */}}
{{- with .Values.tenant }}
{{- if .orgName }}
- name: TENANT_ORG_NAME
  value: {{ .orgName | quote }}
{{- end }}
{{- if .productName }}
- name: TENANT_PRODUCT_NAME
  value: {{ .productName | quote }}
{{- end }}
{{- if .reportingCurrency }}
- name: TENANT_REPORTING_CURRENCY
  value: {{ .reportingCurrency | quote }}
{{- end }}
{{- end }}
{{- /* App-layer Basic Auth (CX-6): defence-in-depth on the destructive
       endpoints. Credentials come from a Secret, never values. Both keys
       must be present for the gate to enable. */}}
{{- if .Values.basicAuth.enabled }}
- name: BASIC_AUTH_USER
  valueFrom:
    secretKeyRef:
      name: {{ .Values.basicAuth.secretName | quote }}
      key: {{ .Values.basicAuth.userKey | quote }}
- name: BASIC_AUTH_PASS
  valueFrom:
    secretKeyRef:
      name: {{ .Values.basicAuth.secretName | quote }}
      key: {{ .Values.basicAuth.passKey | quote }}
{{- end }}
{{- /* Live ManageIQ collector (MIQ-1). Set miq.url to switch the seed/dispatch
       from the synthesized snapshot to live collection; creds + CA from a
       Secret (G-1/G-6 — never verify=False, never inline creds). */}}
{{- with .Values.miq }}
{{- if .url }}
- name: MIQ_URL
  value: {{ .url | quote }}
- name: MIQ_USER
  valueFrom:
    secretKeyRef:
      name: {{ .secretName | quote }}
      key: {{ .userKey | quote }}
- name: MIQ_PASS
  valueFrom:
    secretKeyRef:
      name: {{ .secretName | quote }}
      key: {{ .passKey | quote }}
{{- if .caBundlePath }}
- name: MIQ_CA_BUNDLE
  value: {{ .caBundlePath | quote }}
{{- end }}
{{- end }}
{{- end }}
{{- end -}}
