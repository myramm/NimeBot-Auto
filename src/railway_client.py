import json
import urllib.request
import urllib.error
from typing import Dict, Any, Optional, Tuple

RAILWAY_GRAPHQL_URL = "https://backboard.railway.com/graphql/v2"

def execute_graphql(token: str, query: str, variables: Optional[Dict[str, Any]] = None, timeout: int = 15) -> Dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token.strip()}"
    }
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(RAILWAY_GRAPHQL_URL, data=data_bytes, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            res_body = response.read().decode("utf-8")
            return json.loads(res_body)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        try:
            return json.loads(error_body)
        except Exception:
            return {"errors": [{"message": f"HTTP {e.code}: {e.reason}"}]}
    except Exception as e:
        return {"errors": [{"message": str(e)}]}

def verify_railway_token(token: str) -> Optional[Dict[str, Any]]:
    query = """
    query {
        me {
            id
            email
            name
        }
    }
    """
    res = execute_graphql(token, query)
    if "data" in res and res["data"] and "me" in res["data"] and res["data"]["me"]:
        return res["data"]["me"]
    return None

def list_projects(token: str) -> list:
    query = """
    query {
        projects {
            edges {
                node {
                    id
                    name
                    environments {
                        edges {
                            node {
                                id
                                name
                            }
                        }
                    }
                    services {
                        edges {
                            node {
                                id
                                name
                            }
                        }
                    }
                }
            }
        }
    }
    """
    res = execute_graphql(token, query)
    try:
        edges = res["data"]["projects"]["edges"]
        return [e["node"] for e in edges]
    except Exception:
        return []

def ensure_railway_project(token: str, project_name: str = "file-streamer") -> Tuple[str, str, str]:
    """
    Finds existing project, or finds service 'uploader' or creates/identifies ids.
    Returns (project_id, environment_id, service_id)
    """
    projects = list_projects(token)
    target_project = None
    for p in projects:
        if p["name"] == project_name:
            target_project = p
            break
    
    if not target_project and projects:
        target_project = projects[0]
        
    if not target_project:
        # Create new project if none found
        create_query = """
        mutation projectCreate($input: ProjectCreateInput!) {
            projectCreate(input: $input) {
                id
                name
            }
        }
        """
        res = execute_graphql(token, create_query, {"input": {"name": project_name}})
        p_id = res["data"]["projectCreate"]["id"]
        # re-query project to get default environment
        projects = list_projects(token)
        target_project = next((p for p in projects if p["id"] == p_id), None)
        if not target_project:
            raise RuntimeError(f"Failed to provision Railway project {project_name}")

    proj_id = target_project["id"]
    env_edges = target_project.get("environments", {}).get("edges", [])
    env_id = env_edges[0]["node"]["id"] if env_edges else ""
    
    srv_edges = target_project.get("services", {}).get("edges", [])
    srv_id = ""
    for s in srv_edges:
        if s["node"]["name"] == "uploader":
            srv_id = s["node"]["id"]
            break
    if not srv_id and srv_edges:
        srv_id = srv_edges[0]["node"]["id"]
        
    return proj_id, env_id, srv_id

def set_worker_start_command(token: str, env_id: str, service_id: str, command: str) -> bool:
    query = """
    mutation serviceInstanceUpdate($environmentId: String!, $serviceId: String!, $input: ServiceInstanceUpdateInput!) {
        serviceInstanceUpdate(environmentId: $environmentId, serviceId: $serviceId, input: $input)
    }
    """
    res = execute_graphql(token, query, {
        "environmentId": env_id,
        "serviceId": service_id,
        "input": {"startCommand": command}
    })
    return bool(res.get("data", {}).get("serviceInstanceUpdate"))

def redeploy_worker(token: str, env_id: str, service_id: str) -> bool:
    query = """
    mutation serviceInstanceRedeploy($environmentId: String!, $serviceId: String!) {
        serviceInstanceRedeploy(environmentId: $environmentId, serviceId: $serviceId)
    }
    """
    res = execute_graphql(token, query, {
        "environmentId": env_id,
        "serviceId": service_id
    })
    return bool(res.get("data", {}).get("serviceInstanceRedeploy"))

def stop_worker(token: str, deployment_id: str) -> bool:
    query = """
    mutation deploymentRemove($id: String!) {
        deploymentRemove(id: $id)
    }
    """
    res = execute_graphql(token, query, {"id": deployment_id})
    return bool(res.get("data", {}).get("deploymentRemove"))

def get_latest_deployment(token: str, service_id: str, env_id: str) -> Optional[Dict[str, Any]]:
    query = """
    query service($id: String!) {
        service(id: $id) {
            deployments(first: 1) {
                edges {
                    node {
                        id
                        status
                        createdAt
                    }
                }
            }
        }
    }
    """
    res = execute_graphql(token, query, {"id": service_id})
    try:
        edges = res["data"]["service"]["deployments"]["edges"]
        if edges:
            return edges[0]["node"]
    except Exception:
        pass
    return None
