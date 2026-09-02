{{/*
Generate image name
Usage: {{ include "coelho-llm-rotator.imageName" (dict "appName" "fastapi" "root" .) }}
Images are specified with full registry path in values.yaml
*/}}
{{- define "coelho-llm-rotator.imageName" -}}
{{- index .root.Values .appName "image" -}}
{{- end -}}


{{/*
Common environment variables for all services (non-sensitive)
Credentials are loaded from secret via secretRef
*/}}
{{- define "coelho-llm-rotator.commonEnvVars" -}}
ENVIRONMENT: "{{ .Values.environment }}"
FASTAPI_HOST: "coelho-llm-rotator-fastapi"
{{- end -}}

{{/*
ConfigMap settings
*/}}
{{- define "coelho-llm-rotator.ConfigMapSettings" -}}
kind: ConfigMap
metadata:
  name: coelho-llm-rotator-{{ .appName }}-configmap
  namespace: {{ .root.Release.Namespace }}
{{- end -}}


{{/*
Deployment settings
*/}}
{{- define "coelho-llm-rotator.DeploymentSettings" -}}
kind: Deployment
metadata:
  name: coelho-llm-rotator-{{ .appName }}
  namespace: {{ .root.Release.Namespace }}
  labels:
    app.kubernetes.io/name: {{ .root.Chart.Name }}
    app.kubernetes.io/instance: {{ .root.Release.Name }}
    app.kubernetes.io/version: {{ .root.Chart.AppVersion }}
    app.kubernetes.io/component: {{ .appName }}
    app.kubernetes.io/managed-by: {{ .root.Release.Service }}
{{- end -}}


{{/*
Service settings
*/}}
{{- define "coelho-llm-rotator.ServiceSettings" -}}
kind: Service
metadata:
  name: coelho-llm-rotator-{{ .appName }}
  namespace: {{ .root.Release.Namespace }}
  labels:
    app: coelho-llm-rotator-{{ .appName }}
spec:
  selector:
    app: coelho-llm-rotator-{{ .appName }}
{{- end -}}


{{/*
PVC settings
*/}}
{{- define "coelho-llm-rotator.PVCSettings" -}}
kind: PersistentVolumeClaim
metadata:
  name: coelho-llm-rotator-{{ .appName }}-pvc
  namespace: {{ .root.Release.Namespace }}
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: {{ index .root.Values .appName "storageSize" }}
  storageClassName: {{ index .root.Values .appName "storageClassName" }}
{{- end -}}


{{/*
Deployment spec settings
*/}}
{{- define "coelho-llm-rotator.DeploymentSpecSettings" -}}
selector:
  matchLabels:
    app: coelho-llm-rotator-{{ .appName }}
template:
  metadata:
    labels:
      app: coelho-llm-rotator-{{ .appName }}
  spec:
    {{- if and (eq .root.Values.environment "production") (.root.Values.registry) (.root.Values.registry.imagePullSecret) }}
    imagePullSecrets:
      - name: {{ .root.Values.registry.imagePullSecret }}
    {{- end }}
    #securityContext:
    #  runAsNonRoot: true
    #  runAsUser: 1000
    #  fsGroup: 1000
    containers:
      - name: coelho-llm-rotator-{{ .appName }}
        image: {{ include "coelho-llm-rotator.imageName" (dict "appName" .appName "root" .root) }}
        imagePullPolicy: {{ index .root.Values .appName "imagePullPolicy" }}
        {{- include "coelho-llm-rotator.DeploymentResources" (dict "appName" .appName "root" .root) | nindent 8 }}
        {{- include "coelho-llm-rotator.ProbeSettings" (dict "appName" .appName "root" .root) | nindent 8 }}
        #securityContext:
        #  allowPrivilegeEscalation: false
        #  capabilities:
        #    drop:
        #      - ALL
        #  readOnlyRootFilesystem: false
        envFrom:
          - configMapRef:
              name: coelho-llm-rotator-{{ .appName }}-configmap
        env:
          {{- include "coelho-llm-rotator.secretEnvVars" .root | nindent 10 }}
        volumeMounts:
          - name: llm-secret-volume
            mountPath: /run/secrets/llm
            readOnly: true
    volumes:
      - name: llm-secret-volume
        secret:
          secretName: {{ .root.Values.secretName }}
          optional: true
{{- end -}}


{{/*
Secret environment variables - maps secret keys to env var names
Iterates over secretMappings defined in values.yaml
*/}}
{{- define "coelho-llm-rotator.secretEnvVars" -}}
{{- range .Values.secretMappings }}
- name: {{ .envName }}
  valueFrom:
    secretKeyRef:
      name: {{ $.Values.secretName }}
      key: {{ .key }}
      optional: true
{{- end }}
{{- end -}}


{{- define "coelho-llm-rotator.DeploymentResources" -}}
resources:
  requests:
    memory: {{ index .root.Values .appName "resources" "requests" "memory" }}
    cpu: {{ index .root.Values .appName "resources" "requests" "cpu" }}
  limits:
    memory: {{ index .root.Values .appName "resources" "limits" "memory" }}
    cpu: {{ index .root.Values .appName "resources" "limits" "cpu" }}
{{- end -}}


{{/*
Generate fullname for resources
*/}}
{{- define "coelho-llm-rotator.fullname" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- end }}


{{/*
Common labels
*/}}
{{- define "coelho-llm-rotator.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
{{ include "coelho-llm-rotator.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}


{{/*
Selector labels
*/}}
{{- define "coelho-llm-rotator.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}


{{/*
Service ports settings - ClusterIP for local (Skaffold), full portsSettings for production (ArgoCD)
Usage: {{ include "coelho-llm-rotator.ServicePortsSettings" (dict "appName" "fastapi" "root" .) }}
*/}}
{{- define "coelho-llm-rotator.ServicePortsSettings" -}}
{{- if eq .root.Values.environment "local" }}
  type: ClusterIP
  ports:
    {{- range (index .root.Values .appName "portsSettings" "ports") }}
    - name: {{ .name }}
      port: {{ .port }}
      targetPort: {{ .targetPort }}
      protocol: {{ .protocol }}
    {{- end }}
{{- else }}
  {{- toYaml (index .root.Values .appName "portsSettings") | nindent 2 }}
{{- end }}
{{- end -}}


{{/*
Probe settings - renders all probes (startup, liveness, readiness) for a container
Usage: {{ include "coelho-llm-rotator.ProbeSettings" (dict "appName" "fastapi" "root" .) }}

Probe execution order:
1. startupProbe  - Runs ONLY during startup, disables liveness/readiness until success
2. livenessProbe - Runs after startup succeeds, restarts pod on failure
3. readinessProbe - Runs after startup succeeds, removes from Service on failure
*/}}
{{- define "coelho-llm-rotator.ProbeSettings" -}}
{{- $appConfig := index .root.Values .appName -}}
{{- if $appConfig.startupProbeSettings }}
{{ toYaml $appConfig.startupProbeSettings }}
{{- end }}
{{- if $appConfig.livenessProbeSettings }}
{{ toYaml $appConfig.livenessProbeSettings }}
{{- end }}
{{- if $appConfig.readinessProbeSettings }}
{{ toYaml $appConfig.readinessProbeSettings }}
{{- end }}
{{- end -}}