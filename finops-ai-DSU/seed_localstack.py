import boto3

def seed():
    endpoint = "http://localhost:4566"
    print(f"Connecting to LocalStack at {endpoint}...")
    ec2 = boto3.client(
        "ec2",
        endpoint_url=endpoint,
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test"
    )

    # 1. COMPUTE: Overprovisioned EC2 Instance (m5.2xlarge - $280/mo)
    inst = ec2.run_instances(
        ImageId="ami-df5de72",
        InstanceType="m5.2xlarge",
        MinCount=1,
        MaxCount=1,
        TagSpecifications=[{"ResourceType": "instance", "Tags": [{"Key": "Name", "Value": "prod-api-gateway"}]}]
    )
    inst_id = inst["Instances"][0]["InstanceId"]
    print(f"[+] [COMPUTE] Created Overprovisioned EC2: {inst_id} (m5.2xlarge)")

    # 2. STORAGE: Orphaned 500GB EBS Volume (Unattached gp3 disk - $40/mo waste)
    vol = ec2.create_volume(
        AvailabilityZone="us-east-1a",
        Size=500,
        VolumeType="gp3",
        TagSpecifications=[{"ResourceType": "volume", "Tags": [{"Key": "Name", "Value": "abandoned-db-backup"}]}]
    )
    vol_id = vol["VolumeId"]
    print(f"[+] [STORAGE] Created Orphaned 500GB EBS Volume: {vol_id} (Status: available)")

    # 3. NETWORKING: Unassociated Elastic IP ($3.65/mo idle fee)
    eip = ec2.allocate_address(Domain="vpc")
    print(f"[+] [NETWORKING] Allocated Unassociated Elastic IP: {eip['PublicIp']} (Charging idle fee)")

    print("\n[OK] LocalStack successfully seeded with Compute, Storage, and Networking waste!")

if __name__ == "__main__":
    seed()
