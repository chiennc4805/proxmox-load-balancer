from core.factory import Factory
from core.metadata_handler import MetadataHandler
from core.proxmox_adapter import ProxmoxAdapter
from core.context import RuntimeContext
from config import config

class Controller():

	def __init__(self, metadata_path: str):
		self.metadata_path = metadata_path

  	def execute(self):
		# load config and validate
		metadata_handler = MetadataHandler(self.metadata_path) #load input và validate
		proxmox_adapter = ProxmoxAdapter(
			pve_api_host=config.PVE_API_HOST,
			pve_api_user=config.PVE_API_USER,
			pve_token_name=config.PVE_TOKEN_NAME,
			pve_verify_ssl=config.PVE_VERIFY_SSL,
		)

		context = RuntimeContext(
			request=metadata_handler.metadata,
			proxmox=proxmox_adapter
		)
		
		# schedule
		schedule_config = 
		scheduler = Factory.create_scheduler(...)

		# provision VM
		provision_engine = Factory.create_provisioning_engine(...)
		provision_engine.provision()
		


