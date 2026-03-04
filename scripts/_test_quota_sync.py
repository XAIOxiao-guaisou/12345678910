import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from unittest.mock import AsyncMock, patch
from core.api.state import StateKeeper
from core.pipeline import PipelineOrchestrator

async def main():
    sk = StateKeeper()
    await sk.create_task('test_mock', {'status': 'running', 'gateway': 'seedance', 'novel_id': 't', 'total_files': 1, 'log': [], 'finished': False})

    po = PipelineOrchestrator()
    
    async def simulated_submit(*args, **kwargs):
        # Simulate a quota error being thrown
        print(f"Mocking api submit for gateway seedance")
        raise Exception('Quota Exceeded 401 Unauthorized for test')
        
    async def simulate_frontend_change():
        print('Simulator: waiting 6s to switch gateway')
        await asyncio.sleep(6)
        await sk.update_task('test_mock', {'gateway': 'wan_2_6'}, force_save=True)
        print('Simulator: Switched gateway to wan_2_6!')
        
    async def simulated_submit_wan(*args, **kwargs):
        print("Mock WAN submit successful")
        return "task_123"
        
    async def simulated_check(*args, **kwargs):
        return {"status": "succeeded", "video_url": "http://example.com/mock.mp4"}

    # We need a dynamic mock that returns different things based on the gateway
    class DynamicMockAPI:
        def __init__(self, gw):
            self.gw = gw
            
        async def submit_task(self, *args, **kwargs):
            if 'seedance' in self.gw:
                return await simulated_submit(*args, **kwargs)
            else:
                return await simulated_submit_wan(*args, **kwargs)
                
        async def check_status(self, *args, **kwargs):
            return await simulated_check(*args, **kwargs)

    def mock_get_video_api(gateway, model_name):
        return DynamicMockAPI(gateway)

    with patch('core.services.video_service.factory.get_video_api', side_effect=mock_get_video_api):
        asyncio.create_task(simulate_frontend_change())
        print('Starting pipeline with mock quota error...')
        # Wait for max 12 seconds
        try:
            await asyncio.wait_for(po.run_video_generation('t', ['mock_prompt'], 'seedance', task_id='test_mock'), timeout=15.0)
        except asyncio.TimeoutError:
            print('Test timeout')
    print('Test complete.')

if __name__ == '__main__':
    asyncio.run(main())
