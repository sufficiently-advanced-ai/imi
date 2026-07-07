"use client";

import { useState, useEffect } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  getConfig,
  updateConfig,
  getStatus,
  formatStatus,
  getStatusColor,
  SystemConfig
} from "@/lib/api/command";
import { PageContainer } from "@/components/ui/page-container";

export default function CommandCenter() {
  const [config, setConfig] = useState<SystemConfig | null>(null);
  const [loading, setLoading] = useState(false);
  const [testingConnections, setTestingConnections] = useState(false);
  const [editMode, setEditMode] = useState(false);
  const [formValues, setFormValues] = useState<Record<string, Record<string, string>>>({});
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // Load initial configuration
  useEffect(() => {
    fetchConfig();
  }, []);

  // Fetch the current configuration
  const fetchConfig = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await getConfig();
      setConfig(data);
      setFormValues(JSON.parse(JSON.stringify(data))); // Deep copy for form values
    } catch (err) {
      console.error("Failed to fetch configuration:", err);
      setError("Failed to load configuration");
    } finally {
      setLoading(false);
    }
  };

  // Test all connections
  const handleTestConnections = async () => {
    try {
      setTestingConnections(true);
      setError(null);
      const data = await getStatus();
      
      // Update the config with status information
      if (config) {
        const updatedConfig = { ...config };

        // Type-safe way to update services
        const updateServiceStatus = (serviceName: 'claude' | 'github') => {
          if (data[serviceName] && (serviceName === 'claude' || serviceName === 'github')) {
            if (serviceName === 'claude') {
              updatedConfig.claude.status = data[serviceName].status;
              updatedConfig.claude.error_message = data[serviceName].error_message;
            } else if (serviceName === 'github') {
              updatedConfig.github.status = data[serviceName].status;
              updatedConfig.github.error_message = data[serviceName].error_message;
            }
          }
        };

        // Update known services
        updateServiceStatus('claude');
        updateServiceStatus('github');

        setConfig(updatedConfig);
      }
      
      setSuccessMessage("Connection tests completed");
      setTimeout(() => setSuccessMessage(null), 3000);
    } catch (err) {
      console.error("Failed to test connections:", err);
      setError("Failed to test connections");
    } finally {
      setTestingConnections(false);
    }
  };

  // Handle input changes
  const handleInputChange = (service: string, field: string, value: string) => {
    setFormValues({
      ...formValues,
      [service]: {
        ...formValues[service],
        [field]: value
      }
    });
  };

  // Save configuration changes
  const handleSaveConfig = async () => {
    try {
      setLoading(true);
      setError(null);
      
      // Prepare update payload
      const payload = {
        claude: formValues.claude,
        github: formValues.github
      };
      
      const updatedConfig = await updateConfig(payload);
      setConfig(updatedConfig);
      setEditMode(false);
      setSuccessMessage("Configuration updated successfully");
      setTimeout(() => setSuccessMessage(null), 3000);
      
      // Automatically test connections after updating
      await handleTestConnections();
    } catch (err) {
      console.error("Failed to update configuration:", err);
      setError("Failed to update configuration");
    } finally {
      setLoading(false);
    }
  };

  // Format status display
  const displayStatus = (status: string | undefined) => {
    if (!status) return "Unknown";
    return formatStatus(status);
  };

  // Status indicator component
  const StatusIndicator = ({ status }: { status?: string }) => (
    <span className="flex items-center">
      <span className={`w-2 h-2 rounded-full mr-2 ${getStatusColor(status || 'unknown')}`}></span>
      <span className="font-medium">{displayStatus(status)}</span>
    </span>
  );

  if (loading && !config) {
    return (
      <PageContainer>
        <div className="flex items-center justify-center min-h-[60vh]">
          <div className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-blue-500"></div>
        </div>
      </PageContainer>
    );
  }

  return (
    <PageContainer className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Command Center</h1>
          <p className="text-muted-foreground">
            Configure system settings and monitor imi status
          </p>
        </div>
        <div className="flex space-x-2">
          <Button 
            onClick={handleTestConnections} 
            disabled={testingConnections}
            variant="outline"
          >
            {testingConnections ? "Testing..." : "Test All Connections"}
          </Button>
          {!editMode ? (
            <Button onClick={() => setEditMode(true)}>Edit Configuration</Button>
          ) : (
            <div className="flex space-x-2">
              <Button variant="outline" onClick={() => setEditMode(false)}>Cancel</Button>
              <Button onClick={handleSaveConfig}>Save Changes</Button>
            </div>
          )}
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded">
          {error}
        </div>
      )}

      {successMessage && (
        <div className="bg-green-50 border border-green-200 text-green-700 px-4 py-3 rounded">
          {successMessage}
        </div>
      )}

      <div className="grid gap-6 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>System Status</CardTitle>
            <CardDescription>Current service status</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              <div className="flex justify-between items-center">
                <span>Claude API</span>
                {config && <StatusIndicator status={config.claude.status} />}
              </div>
              {config?.claude.error_message && (
                <div className="text-red-500 text-sm ml-6">
                  {config.claude.error_message}
                </div>
              )}
              
              <div className="flex justify-between items-center">
                <span>GitHub Integration</span>
                {config && <StatusIndicator status={config.github.status} />}
              </div>
              {config?.github.error_message && (
                <div className="text-red-500 text-sm ml-6">
                  {config.github.error_message}
                </div>
              )}

            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Claude Configuration</CardTitle>
            <CardDescription>Claude API settings</CardDescription>
          </CardHeader>
          <CardContent>
            {!editMode ? (
              <div className="space-y-2">
                <div className="flex justify-between">
                  <span>API Key</span>
                  <span className="text-muted-foreground">
                    {config?.claude.api_key || "Not configured"}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span>Model</span>
                  <span className="text-muted-foreground">
                    {config?.claude.model || "Default"}
                  </span>
                </div>
              </div>
            ) : (
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium mb-1">API Key</label>
                  <Input
                    type="password"
                    value={formValues?.claude?.api_key || ""}
                    onChange={(e) => handleInputChange("claude", "api_key", e.target.value)}
                    placeholder="Claude API Key"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-1">Model</label>
                  <Input
                    value={formValues?.claude?.model || ""}
                    onChange={(e) => handleInputChange("claude", "model", e.target.value)}
                    placeholder="claude-sonnet-4-5-20250929"
                  />
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>GitHub Configuration</CardTitle>
            <CardDescription>GitHub integration settings</CardDescription>
          </CardHeader>
          <CardContent>
            {!editMode ? (
              <div className="space-y-2">
                <div className="flex justify-between">
                  <span>Access Token</span>
                  <span className="text-muted-foreground">
                    {config?.github.token || "Not configured"}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span>Repository</span>
                  <span className="text-muted-foreground">
                    {config?.github.repo || "Not configured"}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span>Webhook Secret</span>
                  <span className="text-muted-foreground">
                    {config?.github.webhook_secret || "Not configured"}
                  </span>
                </div>
              </div>
            ) : (
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium mb-1">Access Token</label>
                  <Input
                    type="password"
                    value={formValues?.github?.token || ""}
                    onChange={(e) => handleInputChange("github", "token", e.target.value)}
                    placeholder="GitHub Personal Access Token"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-1">Repository</label>
                  <Input
                    value={formValues?.github?.repo || ""}
                    onChange={(e) => handleInputChange("github", "repo", e.target.value)}
                    placeholder="owner/repo"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-1">Webhook Secret</label>
                  <Input
                    type="password"
                    value={formValues?.github?.webhook_secret || ""}
                    onChange={(e) => handleInputChange("github", "webhook_secret", e.target.value)}
                    placeholder="Webhook Secret (optional)"
                  />
                </div>
              </div>
            )}
          </CardContent>
        </Card>

      </div>
    </PageContainer>
  );
}