"""SD WebUI API兼容层"""
import time
import json
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

router = APIRouter()
vertex_client: Optional[Any] = None

async def generate_image_via_vertex_ai(prompt: str, model: str, size: str, n: int, response_format: str) -> Dict[str, Any]:
    """调用Vertex AI生成图片"""
    if not vertex_client:
        raise HTTPException(status_code=500, detail="Vertex AI client not initialized.")

    messages = [{"role": "user", "content": prompt}]
    response = await vertex_client.complete_chat(
        messages, model, _raw_image_response=True
    )
    return response

def get_vertex_sd_model_ids() -> List[str]:
    """从models.json读取模型列表"""
    try:
        with open("config/models.json", 'r', encoding='utf-8') as f:
            config = json.load(f)
            # 返回文件中定义的完整模型列表
            return config.get('models', [])
    except Exception as e:
        print(f"⚠️ 读取models.json失败: {e}")
        return ["gemini-1.5-pro"]

@router.get("/sdapi/v1/sd-models")
def sd_models():
    """返回支持的模型列表"""
    model_ids = get_vertex_sd_model_ids()
    return [
        {
            "title": mid,
            "model_name": mid,
            "hash": "vertex_proxy_hash",
            "sha256": "vertex_proxy_sha256",
            "filename": f"vertex_proxy/{mid}.safetensors",
            "config": "vertex_proxy_config"
        }
        for mid in model_ids
    ]

@router.get("/sdapi/v1/sd-vae")
def sd_vaes():
    return [
        {"model_name": "Automatic"},
        {"model_name": "None"},
        {"model_name": "Vertex-VAE"},
    ]

@router.get("/sdapi/v1/samplers")
def sd_samplers():
    return [
        {"name": "Euler"},
        {"name": "Euler a"},
        {"name": "DPM++ 2S a Karras"},
        {"name": "DPM++ 2M Karras"},
        {"name": "UniPC"},
    ]

@router.get("/sdapi/v1/options")
def sd_get_options():
    return {}

@router.post("/sdapi/v1/options")
def sd_set_options(request: Dict[str, Any]):
    return {}

@router.get("/sdapi/v1/loras")
@router.get("/sdapi/v1/sd-modules")
@router.get("/sdapi/v1/schedulers")
@router.get("/sdapi/v1/upscalers")
@router.get("/sdapi/v1/latent-upscale-modes")
def sd_empty_list():
    return []

class SDTxt2ImgRequest(BaseModel):
    prompt: str
    negative_prompt: Optional[str] = ""
    sampler_name: Optional[str] = "Euler"
    steps: Optional[int] = 20
    cfg_scale: Optional[float] = 7.0
    width: Optional[int] = 1024
    height: Optional[int] = 1024
    seed: Optional[int] = -1
    override_settings: Optional[Dict[str, Any]] = Field(alias="override_settings", default=None)


@router.post("/sdapi/v1/txt2img")
async def sd_txt2img(request: SDTxt2ImgRequest):
    """代理txt2img请求到Vertex AI"""
    print(f"➡️ txt2img: prompt='{request.prompt}'")

    base_model = "gemini-3-pro-image-preview"
    model_id = base_model
    if request.override_settings and "sd_model_checkpoint" in request.override_settings:
        model_id = request.override_settings["sd_model_checkpoint"]
    
    print(f"ℹ️ 使用模型: {model_id}")

    try:
        openai_response = await generate_image_via_vertex_ai(
            prompt=request.prompt,
            model=model_id,
            size=f"{request.width}x{request.height}",
            n=1,
            response_format="b64_json"
        )
        
        if not openai_response.get("data") or not openai_response["data"][0].get("b64_json"):
            raise HTTPException(status_code=500, detail="Image generation failed to return b64_json data.")
            
        b64_image = openai_response["data"][0]["b64_json"]
        
        sd_info = {
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt,
            "sampler_name": request.sampler_name,
            "steps": request.steps,
            "cfg_scale": request.cfg_scale,
            "width": request.width,
            "height": request.height,
            "seed": request.seed,
            "model": model_id,
            "job_timestamp": int(time.time()),
        }
        
        return {
            "images": [b64_image],
            "parameters": request.dict(),
            "info": json.dumps(sd_info)
        }
        
    except Exception as e:
        print(f"❌ txt2img错误: {e}")
        raise HTTPException(status_code=500, detail=f"代理错误: {str(e)}")


@router.get("/sdapi/v1/progress")
def sd_get_progress():
    return {
        "progress": 0.0,
        "eta_relative": 0.0,
        "state": {
            "skipped": False,
            "interrupted": False,
            "job": "",
            "job_count": 0,
            "job_timestamp": "2025-01-01 00:00:00",
            "sampling_step": 0,
            "sampling_steps": 0
        },
        "current_image": None,
        "textinfo": None
    }