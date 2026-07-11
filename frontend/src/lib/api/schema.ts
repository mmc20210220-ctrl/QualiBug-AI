export const OPENAPI_SPEC_RELATIVE_PATH = "openapi/phase104_api_contract/openapi.json";
export const OPENAPI_SPEC_SHA256 = "7d4bd4557e9803f26e9c886cf4456dfaaddc44f8082e3b6d37f1cd3339c520a4";
export const OPENAPI_GENERATOR = "openapi-typescript@7.13.0";

export type paths = {
    "/api/v1/health": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        
        get: operations["getHealth"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/industry-templates": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        
        get: operations["listIndustryTemplates"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        
        get: operations["listProjects"];
        put?: never;
        
        post: operations["createProject"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        
        get: operations["getProject"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/business-model": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        
        get: operations["getBusinessModel"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        
        patch: operations["patchBusinessModel"];
        trace?: never;
    };
    "/api/v1/projects/{project_id}/business-model/apply-template": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        
        post: operations["applyBusinessTemplate"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/command-center": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        
        get: operations["getCommandCenter"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/environment/config": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        
        patch: operations["patchEnvironmentConfig"];
        trace?: never;
    };
    "/api/v1/projects/{project_id}/environment/preflight": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        
        post: operations["runEnvironmentPreflight"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/environment/readiness": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        
        get: operations["getEnvironmentReadiness"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/live-map": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        
        get: operations["getLiveMap"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/onboarding": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        
        get: operations["getOnboarding"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/reports/executive": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        
        get: operations["getExecutiveReport"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/reports/generate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        
        post: operations["generateExecutiveReport"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/risks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        
        get: operations["listRisks"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/risks/{risk_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        
        get: operations["getRiskDetail"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/test-plan": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        
        get: operations["getTestPlan"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/test-plan/generate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        
        post: operations["generateTestPlan"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/test-runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        
        post: operations["startTestRun"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/test-runs/{run_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        
        get: operations["getTestRun"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/projects/{project_id}/value-metrics": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        
        get: operations["getValueMetrics"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
};
export type webhooks = Record<string, never>;
export type components = {
    schemas: {
        ApiEnvelope: {
            
            data: unknown;
            error: components["schemas"]["ApiError"] | null;
            meta: components["schemas"]["ApiMeta"];
            success: boolean;
        };
        ApiError: {
            code?: string;
            details?: {
                [key: string]: unknown;
            };
            message?: string;
            status?: number;
        };
        ApiMeta: {
            generated_at?: string;
            request_id?: string;
            version?: string;
        };
        ApplyTemplateRequest: {
            approved_by?: string;
            role_config?: {
                [key: string]: boolean;
            };
            template_id: string;
        };
        BusinessModelPatchRequest: {
            approved_by?: string;
            confirmed_business_flows?: {
                [key: string]: unknown;
            }[];
            confirmed_risk_focus?: string[];
            confirmed_roles?: string[];
        };
        EnvironmentConfigPatchRequest: {
            api_smoke_paths?: string[];
            auth_type?: string;
            base_url?: string;
            
            credential_status?: {
                [key: string]: unknown;
            };
            safe_execution_mode?: string;
            session_health_path?: string;
        };
        EnvironmentPreflightRequest: {
            checks?: {
                [key: string]: unknown;
            };
            safe_execution_mode?: string;
        };
        ProjectCreateRequest: {
            customer_name: string;
            
            industry: "manufacturing" | "ecommerce" | "saas" | "finance" | "healthcare" | "government" | "education" | "logistics";
            owner?: string;
            planned_launch_date?: string;
            project_name: string;
            system_name: string;
            system_type?: string;
            test_goal?: string;
        };
        TestPlanGenerateRequest: {
            plan_name?: string;
            safe_execution_mode?: string;
        };
        TestRunStartRequest: {
            findings?: {
                [key: string]: unknown;
            }[];
            run_id?: string;
        };
    };
    responses: never;
    parameters: {
        ProjectId: string;
        RiskId: string;
        RunId: string;
    };
    requestBodies: never;
    headers: never;
    pathItems: never;
};
export type $defs = Record<string, never>;
export interface operations {
    getHealth: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
        };
    };
    listIndustryTemplates: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
        };
    };
    listProjects: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
        };
    };
    createProject: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ProjectCreateRequest"];
            };
        };
        responses: {
            
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
        };
    };
    getProject: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: components["parameters"]["ProjectId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
        };
    };
    getBusinessModel: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: components["parameters"]["ProjectId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
        };
    };
    patchBusinessModel: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: components["parameters"]["ProjectId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["BusinessModelPatchRequest"];
            };
        };
        responses: {
            
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
        };
    };
    applyBusinessTemplate: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: components["parameters"]["ProjectId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ApplyTemplateRequest"];
            };
        };
        responses: {
            
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
        };
    };
    getCommandCenter: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: components["parameters"]["ProjectId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
        };
    };
    patchEnvironmentConfig: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: components["parameters"]["ProjectId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EnvironmentConfigPatchRequest"];
            };
        };
        responses: {
            
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
        };
    };
    runEnvironmentPreflight: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: components["parameters"]["ProjectId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EnvironmentPreflightRequest"];
            };
        };
        responses: {
            
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
        };
    };
    getEnvironmentReadiness: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: components["parameters"]["ProjectId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
        };
    };
    getLiveMap: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: components["parameters"]["ProjectId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
        };
    };
    getOnboarding: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: components["parameters"]["ProjectId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
        };
    };
    getExecutiveReport: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: components["parameters"]["ProjectId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
        };
    };
    generateExecutiveReport: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: components["parameters"]["ProjectId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
        };
    };
    listRisks: {
        parameters: {
            query?: {
                severity?: string;
                business_flow_id?: string;
                status?: string;
                launch_blocking?: boolean;
            };
            header?: never;
            path: {
                project_id: components["parameters"]["ProjectId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
        };
    };
    getRiskDetail: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: components["parameters"]["ProjectId"];
                risk_id: components["parameters"]["RiskId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
        };
    };
    getTestPlan: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: components["parameters"]["ProjectId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
        };
    };
    generateTestPlan: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: components["parameters"]["ProjectId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TestPlanGenerateRequest"];
            };
        };
        responses: {
            
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
        };
    };
    startTestRun: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: components["parameters"]["ProjectId"];
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TestRunStartRequest"];
            };
        };
        responses: {
            
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
        };
    };
    getTestRun: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: components["parameters"]["ProjectId"];
                run_id: components["parameters"]["RunId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
        };
    };
    getValueMetrics: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                project_id: components["parameters"]["ProjectId"];
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            405: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
            
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ApiEnvelope"];
                };
            };
        };
    };
}

