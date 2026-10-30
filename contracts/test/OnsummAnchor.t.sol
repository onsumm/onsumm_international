// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {OnsummAnchor} from "../src/OnsummAnchor.sol";

contract OnsummAnchorTest is Test {
    OnsummAnchor internal anchorContract;
    address internal anchorer = address(0xA11CE);
    address internal stranger = address(0xBEEF);
    bytes32 internal constant ROOT = keccak256("root-1");

    event Anchored(uint256 indexed id, bytes32 root, uint64 fromSeq, uint64 toSeq, uint64 timestamp);

    function setUp() public {
        anchorContract = new OnsummAnchor(anchorer);
    }

    function test_anchorStoresRootAndEmits() public {
        vm.warp(1_798_000_000);
        vm.expectEmit(true, false, false, true);
        emit Anchored(1, ROOT, 1, 412, 1_798_000_000);
        vm.prank(anchorer);
        uint256 id = anchorContract.anchor(ROOT, 1, 412);

        assertEq(id, 1);
        assertEq(anchorContract.count(), 1);
        (bytes32 root, uint64 fromSeq, uint64 toSeq, uint64 ts) = anchorContract.anchors(1);
        assertEq(root, ROOT);
        assertEq(fromSeq, 1);
        assertEq(toSeq, 412);
        assertEq(ts, 1_798_000_000);
    }

    function test_idsIncreaseAndNeverOverwrite() public {
        vm.startPrank(anchorer);
        anchorContract.anchor(ROOT, 1, 10);
        anchorContract.anchor(keccak256("root-2"), 11, 20);
        vm.stopPrank();
        (bytes32 first,,,) = anchorContract.anchors(1);
        assertEq(first, ROOT);
        assertEq(anchorContract.count(), 2);
    }

    function test_onlyAnchorerCanAnchor() public {
        vm.prank(stranger);
        vm.expectRevert(OnsummAnchor.NotAnchorer.selector);
        anchorContract.anchor(ROOT, 1, 2);
    }

    function test_rejectsEmptyRootAndBadRange() public {
        vm.startPrank(anchorer);
        vm.expectRevert(OnsummAnchor.EmptyRoot.selector);
        anchorContract.anchor(bytes32(0), 1, 2);
        vm.expectRevert(OnsummAnchor.BadRange.selector);
        anchorContract.anchor(ROOT, 5, 4);
        vm.stopPrank();
    }

    function test_onlyOwnerChangesAnchorer() public {
        vm.prank(stranger);
        vm.expectRevert(OnsummAnchor.NotOwner.selector);
        anchorContract.setAnchorer(stranger);

        anchorContract.setAnchorer(stranger); // test contract is the owner
        assertEq(anchorContract.anchorer(), stranger);
    }
}
