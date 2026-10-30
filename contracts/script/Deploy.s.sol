// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {OnsummAnchor} from "../src/OnsummAnchor.sol";

/// Deploys OnsummAnchor with the anchor wallet as its only writer.
///   ANCHORER=0x... forge script script/Deploy.s.sol --rpc-url $RPC_URL \
///     --private-key $DEPLOYER_KEY --broadcast
/// Then set ANCHOR_CONTRACT to the printed address to switch the API to contract mode.
contract Deploy is Script {
    function run() external returns (OnsummAnchor deployed) {
        address anchorer = vm.envAddress("ANCHORER");
        vm.startBroadcast();
        deployed = new OnsummAnchor(anchorer);
        vm.stopBroadcast();
        console2.log("OnsummAnchor deployed at", address(deployed));
    }
}
